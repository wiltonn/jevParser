"""Creating and executing comparisons, pairwise and as timelines."""

from __future__ import annotations

import json
import zlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from jev_diff.judge.run import band, lens_value, lenses_from
from jev_diff.pipeline import compare
from jev_diff.rules import PromptContext

from ..db import SessionLocal
from ..db.models import (
    ChangeEventRow,
    Comparison,
    ComparisonResult,
    Document,
    ModelYear,
    RulesetVersion,
    Timeline,
    VehicleModel,
    utcnow,
)
from ..jobs.queue import JobContext, enqueue
from . import settings_store
from .blobstore import blob_path
from .judgment_store import SqlJudgmentStore
from .rulesets import load_rules, resolve_for_years


class ComparisonError(ValueError):
    pass


def primary_document(session: Session, my: ModelYear) -> Document:
    docs = session.scalars(select(Document).where(
        Document.model_year_id == my.id, Document.detection_status == "confirmed")
        .order_by(Document.is_primary.desc(), Document.uploaded_at.desc())).all()
    if not docs:
        raise ComparisonError(f"no confirmed document for {my.model.name} {my.year}")
    return docs[0]


def create(session: Session, *, model_id: int, old_year_id: int, new_year_id: int,
           old_document_id: int | None = None, new_document_id: int | None = None,
           ruleset_version_id: int | None = None, judge: bool = True,
           workers: int | None = None, user_id: int | None = None,
           timeline: Timeline | None = None, position: int | None = None) -> Comparison:
    model = session.get(VehicleModel, model_id)
    old_year, new_year = session.get(ModelYear, old_year_id), session.get(ModelYear, new_year_id)
    if model is None or old_year is None or new_year is None:
        raise ComparisonError("unknown model or model year")
    if old_year.model_id != model.id or new_year.model_id != model.id:
        raise ComparisonError("both years must belong to the model")
    if old_year.year >= new_year.year:
        raise ComparisonError("the old year must come before the new year")

    old_doc = session.get(Document, old_document_id) if old_document_id \
        else primary_document(session, old_year)
    new_doc = session.get(Document, new_document_id) if new_document_id \
        else primary_document(session, new_year)
    if old_doc is None or new_doc is None:
        raise ComparisonError("unknown document")
    if old_doc.model_year_id != old_year.id or new_doc.model_year_id != new_year.id:
        raise ComparisonError("each document must belong to its model year")

    if ruleset_version_id is not None:
        version = session.get(RulesetVersion, ruleset_version_id)
        if version is None or version.status == "archived":
            raise ComparisonError("unknown or archived ruleset version")
    else:
        found = resolve_for_years(session, [old_year, new_year],
                                  {old_doc.format, new_doc.format})
        if not found:
            raise ComparisonError(
                f"no published ruleset covers {model.brand.oem.name} "
                f"{old_year.year}-{new_year.year}")
        version = found[0].version

    comparison = Comparison(
        model_id=model.id, old_model_year_id=old_year.id, new_model_year_id=new_year.id,
        old_document_id=old_doc.id, new_document_id=new_doc.id,
        ruleset_version_id=version.id,
        options={"judge": judge,
                 "workers": workers or settings_store.get(session, "judge.workers")},
        created_by=user_id, timeline=timeline, timeline_position=position,
    )
    session.add(comparison)
    session.flush()
    enqueue(session, "run_comparison", {"comparison_id": comparison.id},
            comparison_id=comparison.id, user_id=user_id)
    return comparison


def create_timeline(session: Session, *, model_id: int, year_ids: list[int],
                    judge: bool = True, user_id: int | None = None,
                    name: str | None = None) -> Timeline:
    years = sorted((session.get(ModelYear, y) for y in year_ids), key=lambda y: y.year)
    if len(years) < 2 or any(y is None for y in years):
        raise ComparisonError("a timeline needs at least two known model years")
    model = session.get(VehicleModel, model_id)
    timeline = Timeline(
        model_id=model_id, created_by=user_id,
        name=name or f"{model.name} {years[0].year}–{years[-1].year}")
    session.add(timeline)
    session.flush()
    for i, (a, b) in enumerate(zip(years, years[1:])):
        create(session, model_id=model_id, old_year_id=a.id, new_year_id=b.id,
               judge=judge, user_id=user_id, timeline=timeline, position=i)
    return timeline


def rerun(session: Session, comparison: Comparison, user_id: int | None = None):
    comparison.status, comparison.error, comparison.finished_at = "queued", None, None
    return enqueue(session, "run_comparison", {"comparison_id": comparison.id},
                   comparison_id=comparison.id, user_id=user_id)


def context_for(comparison: Comparison) -> PromptContext:
    model = comparison.model
    return PromptContext(
        old_year=str(comparison.old_model_year.year),
        new_year=str(comparison.new_model_year.year),
        oem=model.brand.oem.prompt_name, brand=model.brand.name, model=model.name)


def execute(ctx: JobContext) -> dict[str, Any]:
    comparison_id = ctx.params["comparison_id"]
    with SessionLocal() as session:
        c = session.get(Comparison, comparison_id)
        c.status, c.error = "running", None
        session.commit()
        rules = load_rules(c.ruleset_version)
        context = context_for(c)
        path_a = str(blob_path(c.old_document.blob_sha256))
        path_b = str(blob_path(c.new_document.blob_sha256))
        judge = bool(c.options.get("judge"))
        workers = int(c.options.get("workers") or 8)
        api = settings_store.api_config(session)
        ruleset_hash = c.ruleset_version.content_hash

    store = SqlJudgmentStore(SessionLocal)
    result = compare(
        path_a, path_b, label_a=context.old_year, label_b=context.new_year,
        rules=rules, context=context, store=store, judge=judge, workers=workers,
        api=api, progress=lambda stage, f: ctx.progress(stage, f),
        cancelled=ctx.cancelled,
    )
    payload = result.payload
    payload["source"] = {"old": f"document:{c.old_document_id}",
                         "new": f"document:{c.new_document_id}"}
    lenses = lenses_from(rules)
    judgments = result.judgments

    with SessionLocal() as session:
        c = session.get(Comparison, comparison_id)
        if c.result is not None:
            session.delete(c.result)
        session.query(ChangeEventRow).filter_by(comparison_id=c.id).delete()
        session.flush()
        bands = {name: {} for name in lenses}
        for event in result.events:
            values = {name: round(lens_value(event, lens), 4) for name, lens in lenses.items()}
            for name, lens in lenses.items():
                b = band(event, lens)
                bands[name][b] = bands[name].get(b, 0) + 1
            session.add(ChangeEventRow(
                comparison_id=c.id, eid=event.eid, kind=event.kind, sheet=event.sheet,
                also_on=list(event.also_on), code=event.code,
                description=event.description, detail=event.detail,
                disposition=event.disposition, order_affecting=event.order_affecting,
                lens_values=values))
        kinds: dict[str, int] = {}
        for event in result.events:
            kinds[event.kind] = kinds.get(event.kind, 0) + 1
        summary = dict(payload["summary"], events=len(result.events), kinds=kinds,
                       order_affecting=sum(1 for e in result.events if e.order_affecting),
                       bands=bands if judge else None)
        c.result = ComparisonResult(
            payload_z=zlib.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), 6),
            summary=summary,
            usage=judgments.usage if judgments else None,
            cache_hits=judgments.cache_hits if judgments else 0,
            cache_misses=judgments.cache_misses if judgments else 0,
            errors=judgments.errors if judgments else None,
            ruleset_hash=ruleset_hash,
        )
        c.status, c.finished_at = "succeeded", utcnow()
        session.commit()
    if judgments and judgments.errors:
        return {"events": len(result.events), "errors": judgments.errors[:20]}
    return {"events": len(result.events)}


def payload_bytes(result: ComparisonResult) -> bytes:
    return zlib.decompress(result.payload_z)


def payload_of(result: ComparisonResult) -> dict:
    return json.loads(payload_bytes(result))
