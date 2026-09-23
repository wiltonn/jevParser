"""Downloadable artifacts of a finished comparison."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from jev_diff.judge.run import band, lens_value, lenses_from
from jev_diff.pipeline import compare
from jev_diff.render.html import render

from ..db import SessionLocal
from ..db.models import Comparison, Export
from ..jobs.queue import JobContext
from .blobstore import blob_path, save_bytes
from .comparisons import context_for, payload_of
from .rulesets import load_rules

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ExportError(ValueError):
    pass


def _stem(c: Comparison) -> str:
    m = c.model
    return (f"{c.old_model_year.year}-{c.new_model_year.year} "
            f"{m.brand.name} {m.name}").replace("/", "-")


def execute(ctx: JobContext) -> dict:
    comparison_id, kind = ctx.params["comparison_id"], ctx.params["kind"]
    lens_name = ctx.params.get("lens") or "ordering"
    with SessionLocal() as session:
        c = session.get(Comparison, comparison_id)
        if c.result is None:
            raise ExportError("the comparison has no result yet")
        payload = payload_of(c.result)
        stem = _stem(c)
        if kind == "html":
            ctx.progress("render", 0.5, force=True)
            blob = save_bytes(session, render(payload).encode("utf-8"), "text/html")
            filename = f"{stem} report.html"
        elif kind == "xlsx":
            if c.new_document.format != "xlsx":
                raise ExportError("an annotated workbook needs an Excel new-year document")
            ctx.progress("rebuild", 0.2, force=True)
            rules = load_rules(c.ruleset_version)
            new_path = str(blob_path(c.new_document.blob_sha256))
            # The deterministic stages are cheap and reproducible, so rebuild the
            # events and attach the judgments stored with the payload rather than
            # keeping live objects around.  No judgment is re-asked.
            result = compare(str(blob_path(c.old_document.blob_sha256)), new_path,
                             label_a=str(c.old_model_year.year),
                             label_b=str(c.new_model_year.year),
                             rules=rules, context=context_for(c))
            stored = {e["eid"]: e["judgments"] for e in payload["events"]}
            for event in result.events:
                event.judgments = {q: {k: v for k, v in a.items() if v is not None}
                                   for q, a in (stored.get(event.eid) or {}).items()}
            lenses = lenses_from(rules)
            if lens_name not in lenses:
                raise ExportError(f"unknown lens {lens_name!r}")
            lens = lenses[lens_name]
            from jev_diff.render.xlsx_annotate import annotate

            ctx.progress("annotate", 0.6, force=True)
            with tempfile.TemporaryDirectory() as tmp:
                # openpyxl insists on an Excel extension; blobs are named by hash.
                source = Path(tmp) / "source.xlsx"
                shutil.copyfile(new_path, source)
                out = Path(tmp) / "annotated.xlsx"
                annotate(str(source), str(out), result.events,
                         bands={i: band(e, lens) for i, e in enumerate(result.events)},
                         values={i: round(lens_value(e, lens), 2)
                                 for i, e in enumerate(result.events)})
                blob = save_bytes(session, out.read_bytes(), XLSX_MIME)
            filename = f"{stem} annotated ({lens_name}).xlsx"
        else:
            raise ExportError(f"unknown export kind {kind!r}")
        export = Export(comparison_id=c.id, kind=kind,
                        lens=lens_name if kind == "xlsx" else None,
                        blob_sha256=blob.sha256, filename=filename)
        session.add(export)
        session.commit()
        return {"export_id": export.id, "filename": filename}
