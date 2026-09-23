"""Plain-dict views of the models, shared by the routers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..db.models import (
    Comparison,
    Document,
    Export,
    Job,
    ModelYear,
    ParseRun,
    Ruleset,
    RulesetVersion,
    User,
)


def ts(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def user(u: User) -> dict[str, Any]:
    return {"id": u.id, "email": u.email, "display_name": u.display_name,
            "role": u.role, "is_active": u.is_active}


def model_year(my: ModelYear) -> dict[str, Any]:
    m = my.model
    return {"id": my.id, "year": my.year, "variant_label": my.variant_label,
            "model_id": m.id, "model": m.name, "brand_id": m.brand.id,
            "brand": m.brand.name, "oem_id": m.brand.oem.id, "oem": m.brand.oem.name}


def parse_run(run: ParseRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    v = run.ruleset_version
    return {
        "id": run.id, "status": run.status, "fatal": run.fatal_count,
        "warnings": run.warn_count, "error": run.error,
        "ruleset_version_id": v.id, "ruleset": v.ruleset.name, "ruleset_version": v.version,
        "sheets": run.sheet_summary, "relations": run.relation_histogram,
        "started_at": ts(run.started_at), "finished_at": ts(run.finished_at),
    }


def document(d: Document, *, detail: bool = False) -> dict[str, Any]:
    latest = d.parse_runs[0] if d.parse_runs else None
    out = {
        "id": d.id, "filename": d.original_filename, "format": d.format,
        "size": d.blob.size, "sha256": d.blob_sha256,
        "model_year": model_year(d.model_year) if d.model_year else None,
        "detection_status": d.detection_status, "is_primary": d.is_primary,
        "uploaded_at": ts(d.uploaded_at), "notes": d.notes,
        "parse": parse_run(latest),
        "detection": d.detection if detail or d.detection_status == "pending" else None,
    }
    if detail:
        out["parse_runs"] = [parse_run(r) for r in d.parse_runs]
    return out


def ruleset_version(v: RulesetVersion, *, body: bool = False) -> dict[str, Any]:
    out = {
        "id": v.id, "ruleset_id": v.ruleset_id, "version": v.version, "status": v.status,
        "schema_version": v.schema_version, "content_hash": v.content_hash,
        "notes": v.notes, "created_at": ts(v.created_at), "published_at": ts(v.published_at),
    }
    if body:
        out["body"] = v.body
    return out


def ruleset(r: Ruleset, *, versions: bool = True) -> dict[str, Any]:
    published = [v for v in r.versions if v.status == "published"]
    out = {
        "id": r.id, "name": r.name, "description": r.description,
        "oem_id": r.oem_id, "oem": r.oem.name,
        "brand_id": r.brand_id, "brand": r.brand.name if r.brand else None,
        "model_id": r.model_id, "model": r.model.name if r.model else None,
        "year_from": r.year_from, "year_to": r.year_to, "format": r.format,
        "priority": r.priority, "created_at": ts(r.created_at),
        "published_version": max((v.version for v in published), default=None),
        "has_draft": any(v.status == "draft" for v in r.versions),
        "cloned_from_version_id": r.cloned_from_version_id,
    }
    if versions:
        out["versions"] = [ruleset_version(v) for v in reversed(r.versions)]
    return out


def job(j: Job) -> dict[str, Any]:
    return {
        "id": j.id, "type": j.type, "status": j.status, "stage": j.stage,
        "progress": j.progress, "message": j.message, "error": j.error,
        "result": j.result, "params": j.params, "cancel_requested": j.cancel_requested,
        "comparison_id": j.comparison_id, "document_id": j.document_id,
        "created_at": ts(j.created_at), "started_at": ts(j.started_at),
        "finished_at": ts(j.finished_at),
    }


def comparison(c: Comparison, *, latest_job: Job | None = None) -> dict[str, Any]:
    r = c.result
    return {
        "id": c.id, "status": c.status, "error": c.error,
        "model_id": c.model_id, "model": c.model.name, "brand": c.model.brand.name,
        "oem": c.model.brand.oem.name,
        "old": {"year_id": c.old_model_year_id, "year": c.old_model_year.year,
                "document_id": c.old_document_id,
                "filename": c.old_document.original_filename},
        "new": {"year_id": c.new_model_year_id, "year": c.new_model_year.year,
                "document_id": c.new_document_id,
                "filename": c.new_document.original_filename},
        "ruleset": {"id": c.ruleset_version.ruleset_id, "version_id": c.ruleset_version_id,
                    "name": c.ruleset_version.ruleset.name,
                    "version": c.ruleset_version.version},
        "options": c.options, "timeline_id": c.timeline_id,
        "timeline_position": c.timeline_position,
        "created_at": ts(c.created_at), "finished_at": ts(c.finished_at),
        "summary": r.summary if r else None,
        "usage": r.usage if r else None,
        "cache": {"hits": r.cache_hits, "misses": r.cache_misses} if r else None,
        "judge_errors": (r.errors or [])[:10] if r else None,
        "job": job(latest_job) if latest_job else None,
    }


def export(e: Export) -> dict[str, Any]:
    return {"id": e.id, "kind": e.kind, "lens": e.lens, "filename": e.filename,
            "created_at": ts(e.created_at)}
