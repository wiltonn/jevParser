"""Uploading, identifying and parsing order guides."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from jev_diff.formats import UnsupportedFormat, detect_format
from jev_diff.model.canonical import Workbook

from ..db.models import (
    Document,
    ModelYear,
    ParseRun,
    ParseWarning,
    RulesetVersion,
    utcnow,
)
from ..jobs.queue import enqueue
from . import catalog, detection
from .blobstore import blob_path, save_blob
from .rulesets import load_rules, resolve_for_years

MIME = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


class DocumentError(ValueError):
    pass


def ingest(session: Session, stream: BinaryIO, filename: str,
           user_id: int | None = None) -> Document:
    """Store an upload and suggest what it is.  Stays ``pending`` until confirmed."""
    ext = Path(filename).suffix.lower().lstrip(".")
    blob = save_blob(session, stream, MIME.get(ext, "application/octet-stream"))
    path = str(blob_path(blob.sha256))
    try:
        fmt = detect_format(path, filename)
    except UnsupportedFormat as exc:
        raise DocumentError(str(exc)) from exc
    blob.mime = MIME.get(fmt, blob.mime)

    doc = Document(blob_sha256=blob.sha256, original_filename=filename, format=fmt,
                   uploaded_by=user_id, detection_status="pending")
    doc.detection = detection.detect(session, path, filename, fmt)
    session.add(doc)
    session.flush()

    # The same file already confirmed elsewhere is the strongest signal of all.
    earlier = session.scalar(
        select(Document).where(Document.blob_sha256 == blob.sha256,
                               Document.id != doc.id,
                               Document.model_year_id.is_not(None)))
    if earlier is not None:
        doc.detection = dict(doc.detection, duplicate_of=earlier.id)
    return doc


def confirm(session: Session, doc: Document, *, model_year_id: int | None = None,
            oem: str | None = None, brand: str | None = None, model: str | None = None,
            year: int | None = None, make_primary: bool | None = None,
            user_id: int | None = None) -> Document:
    """Assign the document to a model year (creating catalog entries if named)."""
    if model_year_id is not None:
        my = session.get(ModelYear, model_year_id)
        if my is None:
            raise DocumentError(f"no model year {model_year_id}")
    else:
        if not (oem and brand and model and year):
            raise DocumentError("give a model_year_id, or oem + brand + model + year")
        o = catalog.ensure_oem(session, oem)
        b = catalog.ensure_brand(session, o, brand)
        m = catalog.ensure_model(session, b, model)
        my = catalog.ensure_year(session, m, int(year))
    doc.model_year_id = my.id
    doc.model_year = my
    doc.detection_status = "confirmed"
    has_primary = session.scalar(select(Document.id).where(
        Document.model_year_id == my.id, Document.is_primary, Document.id != doc.id))
    if make_primary or (make_primary is None and not has_primary):
        set_primary(session, doc)
    session.flush()
    queue_parse(session, doc, user_id=user_id)
    return doc


def set_primary(session: Session, doc: Document) -> None:
    for other in session.scalars(select(Document).where(
            Document.model_year_id == doc.model_year_id, Document.is_primary)):
        other.is_primary = False
    doc.is_primary = True


def resolve_version(session: Session, doc: Document) -> RulesetVersion:
    if doc.model_year is None:
        raise DocumentError("confirm the document's model year first")
    found = resolve_for_years(session, [doc.model_year], {doc.format})
    if not found:
        raise DocumentError(
            f"no published ruleset covers {doc.model_year.model.brand.oem.name} "
            f"{doc.model_year.year} ({doc.format})")
    return found[0].version


def queue_parse(session: Session, doc: Document, version: RulesetVersion | None = None,
                user_id: int | None = None):
    version = version or resolve_version(session, doc)
    run = session.scalar(select(ParseRun).where(ParseRun.document_id == doc.id,
                                                ParseRun.ruleset_version_id == version.id))
    if run is None:
        run = ParseRun(document_id=doc.id, ruleset_version_id=version.id)
        session.add(run)
    run.status = "queued"
    session.flush()
    return enqueue(session, "parse_document",
                   {"parse_run_id": run.id}, document_id=doc.id, user_id=user_id)


def label_for(doc: Document) -> str:
    return str(doc.model_year.year) if doc.model_year else Path(doc.original_filename).stem[:4]


def summarize(book: Workbook) -> tuple[list[dict], dict[str, int]]:
    sheets = [
        {"name": s.name, "kind": s.kind, "rows": len(s.rows), "columns": len(s.columns),
         "footnotes": len(s.footnotes), "images": len(s.images)}
        for s in book.sheets.values()
    ]
    relations: Counter[str] = Counter()
    for s in book.sheets.values():
        for row in s.rows:
            for cell in row.values.values():
                for c in cell.conditions:
                    relations[c.relation] += 1
    return sheets, dict(relations.most_common())


def unstructured_examples(book: Workbook, limit: int = 40) -> list[dict]:
    seen: dict[str, dict] = {}
    for s in book.sheets.values():
        for row in s.rows:
            for cell in row.values.values():
                for c in cell.conditions:
                    if c.relation == "unstructured" and c.raw not in seen:
                        seen[c.raw] = {"text": c.raw, "sheet": s.name, "code": row.code}
                        if len(seen) >= limit:
                            return list(seen.values())
    return list(seen.values())


def execute_parse(session: Session, run: ParseRun, progress=None) -> ParseRun:
    from jev_diff.pipeline import parse_document

    doc = run.document
    run.status, run.started_at, run.error = "running", utcnow(), None
    run.warnings.clear()
    session.commit()
    try:
        book = parse_document(str(blob_path(doc.blob_sha256)), label_for(doc),
                              load_rules(run.ruleset_version), fmt=doc.format)
    except Exception as exc:
        run.status, run.error, run.finished_at = "failed", f"{type(exc).__name__}: {exc}", utcnow()
        session.commit()
        raise
    run.sheet_summary, run.relation_histogram = summarize(book)
    for w in book.warnings:
        run.warnings.append(ParseWarning(kind=w.kind, severity=w.severity,
                                         message=w.message, where=w.where))
    run.fatal_count = sum(1 for w in book.warnings if w.severity == "fatal")
    run.warn_count = len(book.warnings) - run.fatal_count
    run.status = "fatal" if run.fatal_count else "warnings" if run.warn_count else "ok"
    run.finished_at = utcnow()
    session.commit()
    return run


def latest_run(doc: Document) -> ParseRun | None:
    return doc.parse_runs[0] if doc.parse_runs else None
