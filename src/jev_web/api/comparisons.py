from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import audit, current_user, get_session
from ..db.models import (
    ChangeEventRow,
    Comparison,
    Document,
    Export,
    Job,
    ModelYear,
    Timeline,
    User,
    VehicleModel,
)
from ..jobs.queue import enqueue
from ..services import comparisons as svc
from ..services.blobstore import blob_path
from ..services.rulesets import resolve_for_years
from . import serialize as S

router = APIRouter(tags=["comparisons"])


class ComparisonIn(BaseModel):
    model_id: int
    old_year_id: int
    new_year_id: int
    old_document_id: int | None = None
    new_document_id: int | None = None
    ruleset_version_id: int | None = None
    judge: bool = True
    workers: int | None = None


class TimelineIn(BaseModel):
    model_id: int
    year_ids: list[int]
    judge: bool = True
    name: str | None = None


class ExportIn(BaseModel):
    kind: str
    lens: str | None = None


def _latest_job(session: Session, comparison_id: int) -> Job | None:
    return session.scalar(select(Job).where(Job.comparison_id == comparison_id,
                                            Job.type == "run_comparison")
                          .order_by(Job.id.desc()).limit(1))


def _get(session: Session, cid: int) -> Comparison:
    c = session.get(Comparison, cid)
    if c is None:
        raise HTTPException(404, "no such comparison")
    return c


@router.get("/rulesets/resolve")
def resolve(model_id: int, year_ids: str, session: Session = Depends(get_session, scope="function"),
            _: User = Depends(current_user)):
    """Which ruleset versions would apply to these years (comma-separated ids)."""
    ids = [int(x) for x in year_ids.split(",") if x]
    years = [session.get(ModelYear, i) for i in ids]
    if not years or any(y is None or y.model_id != model_id for y in years):
        raise HTTPException(400, "years must belong to the model")
    formats = set()
    for y in years:
        doc = session.scalar(select(Document).where(
            Document.model_year_id == y.id, Document.detection_status == "confirmed")
            .order_by(Document.is_primary.desc()))
        if doc:
            formats.add(doc.format)
    return [c.as_dict() for c in resolve_for_years(session, years, formats or {"xlsx"})]


@router.post("/comparisons", status_code=201)
def create(body: ComparisonIn, session: Session = Depends(get_session, scope="function"),
           user: User = Depends(current_user)):
    c = svc.create(session, **body.model_dump(), user_id=user.id)
    audit(session, user, "create", "comparison", c.id)
    session.flush()
    return S.comparison(c, latest_job=_latest_job(session, c.id))


@router.get("/comparisons")
def list_comparisons(model_id: int | None = None, limit: int = 100,
                     session: Session = Depends(get_session, scope="function"), _: User = Depends(current_user)):
    q = select(Comparison).order_by(Comparison.id.desc()).limit(min(limit, 500))
    if model_id:
        q = q.where(Comparison.model_id == model_id)
    return [S.comparison(c) for c in session.scalars(q)]


@router.get("/comparisons/{cid}")
def get_comparison(cid: int, session: Session = Depends(get_session, scope="function"),
                   _: User = Depends(current_user)):
    return S.comparison(_get(session, cid), latest_job=_latest_job(session, cid))


@router.get("/comparisons/{cid}/payload")
def payload(cid: int, session: Session = Depends(get_session, scope="function"),
            _: User = Depends(current_user)):
    c = _get(session, cid)
    if c.result is None:
        raise HTTPException(409, "the comparison has not finished")
    return Response(svc.payload_bytes(c.result), media_type="application/json")


@router.get("/comparisons/{cid}/events")
def events(cid: int, kind: str | None = None, sheet: str | None = None,
           q: str | None = None, order_affecting: bool | None = None,
           lens: str | None = None, session: Session = Depends(get_session, scope="function"),
           _: User = Depends(current_user)):
    _get(session, cid)
    query = select(ChangeEventRow).where(ChangeEventRow.comparison_id == cid)
    if kind:
        query = query.where(ChangeEventRow.kind == kind)
    if sheet:
        query = query.where(ChangeEventRow.sheet == sheet)
    if order_affecting is not None:
        query = query.where(ChangeEventRow.order_affecting == order_affecting)
    if q:
        like = f"%{q}%"
        query = query.where(ChangeEventRow.description.ilike(like)
                            | ChangeEventRow.code.ilike(like)
                            | ChangeEventRow.detail.ilike(like))
    rows = list(session.scalars(query))
    if lens:
        rows.sort(key=lambda r: -((r.lens_values or {}).get(lens) or 0.0))
    return [{"eid": r.eid, "kind": r.kind, "sheet": r.sheet, "also_on": r.also_on,
             "code": r.code, "description": r.description, "detail": r.detail,
             "disposition": r.disposition, "order_affecting": r.order_affecting,
             "lens_values": r.lens_values} for r in rows]


@router.post("/comparisons/{cid}/rerun")
def rerun(cid: int, session: Session = Depends(get_session, scope="function"),
          user: User = Depends(current_user)):
    c = _get(session, cid)
    if c.status in ("queued", "running"):
        raise HTTPException(409, "already running")
    job = svc.rerun(session, c, user.id)
    audit(session, user, "rerun", "comparison", c.id)
    return S.job(job)


@router.delete("/comparisons/{cid}", status_code=204)
def delete(cid: int, session: Session = Depends(get_session, scope="function"),
           user: User = Depends(current_user)):
    c = _get(session, cid)
    if c.status in ("queued", "running"):
        raise HTTPException(409, "cancel the run first")
    audit(session, user, "delete", "comparison", c.id)
    session.delete(c)


@router.post("/comparisons/{cid}/exports", status_code=202)
def create_export(cid: int, body: ExportIn, session: Session = Depends(get_session, scope="function"),
                  user: User = Depends(current_user)):
    c = _get(session, cid)
    if c.result is None:
        raise HTTPException(409, "the comparison has not finished")
    if body.kind not in ("html", "xlsx"):
        raise HTTPException(400, "kind must be html or xlsx")
    job = enqueue(session, "export", {"comparison_id": c.id, "kind": body.kind,
                                      "lens": body.lens}, comparison_id=c.id, user_id=user.id)
    return S.job(job)


@router.get("/comparisons/{cid}/exports")
def list_exports(cid: int, session: Session = Depends(get_session, scope="function"),
                 _: User = Depends(current_user)):
    _get(session, cid)
    return [S.export(e) for e in session.scalars(
        select(Export).where(Export.comparison_id == cid).order_by(Export.id.desc()))]


@router.get("/exports/{export_id}/download")
def download_export(export_id: int, session: Session = Depends(get_session, scope="function"),
                    _: User = Depends(current_user)):
    e = session.get(Export, export_id)
    if e is None or e.blob_sha256 is None:
        raise HTTPException(404, "no such export")
    mime = "text/html" if e.kind == "html" else \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return FileResponse(blob_path(e.blob_sha256), media_type=mime, filename=e.filename)


# -- timelines ------------------------------------------------------------------

@router.post("/timelines", status_code=201)
def create_timeline(body: TimelineIn, session: Session = Depends(get_session, scope="function"),
                    user: User = Depends(current_user)):
    t = svc.create_timeline(session, **body.model_dump(), user_id=user.id)
    audit(session, user, "create", "timeline", t.id)
    session.flush()
    return timeline(t.id, session, user)


@router.get("/timelines")
def list_timelines(model_id: int | None = None, session: Session = Depends(get_session, scope="function"),
                   _: User = Depends(current_user)):
    q = select(Timeline).order_by(Timeline.id.desc())
    if model_id:
        q = q.where(Timeline.model_id == model_id)
    return [{"id": t.id, "name": t.name, "model_id": t.model_id,
             "steps": len(t.comparisons), "created_at": S.ts(t.created_at)}
            for t in session.scalars(q)]


@router.get("/timelines/{tid}")
def timeline(tid: int, session: Session = Depends(get_session, scope="function"),
             _: User = Depends(current_user)):
    t = session.get(Timeline, tid)
    if t is None:
        raise HTTPException(404, "no such timeline")
    steps = [S.comparison(c, latest_job=_latest_job(session, c.id)) for c in t.comparisons]
    # Codes that changed in more than one step are the interesting ones.
    counts: dict[str, list[int]] = {}
    for c in t.comparisons:
        for row in session.scalars(select(ChangeEventRow).where(
                ChangeEventRow.comparison_id == c.id, ChangeEventRow.code.is_not(None))):
            steps_for = counts.setdefault(row.code, [])
            if c.timeline_position not in steps_for:
                steps_for.append(c.timeline_position)
    recurring = sorted(((code, pos) for code, pos in counts.items() if len(pos) > 1),
                       key=lambda x: (-len(x[1]), x[0]))
    return {"id": t.id, "name": t.name,
            "model": {"id": t.model.id, "name": t.model.name,
                      "brand": t.model.brand.name},
            "steps": steps,
            "recurring_codes": [{"code": c, "steps": p} for c, p in recurring[:200]]}


@router.get("/timelines/{tid}/codes/{code}")
def code_history(tid: int, code: str, session: Session = Depends(get_session, scope="function"),
                 _: User = Depends(current_user)):
    t = session.get(Timeline, tid)
    if t is None:
        raise HTTPException(404, "no such timeline")
    out = []
    for c in t.comparisons:
        rows = session.scalars(select(ChangeEventRow).where(
            ChangeEventRow.comparison_id == c.id, ChangeEventRow.code == code)).all()
        out.append({"comparison_id": c.id, "old": c.old_model_year.year,
                    "new": c.new_model_year.year, "status": c.status,
                    "events": [{"eid": r.eid, "kind": r.kind, "sheet": r.sheet,
                                "description": r.description, "detail": r.detail,
                                "lens_values": r.lens_values} for r in rows]})
    return {"code": code, "steps": out}


@router.get("/models/{model_id}/codes/{code}")
def model_code_history(model_id: int, code: str, session: Session = Depends(get_session, scope="function"),
                       _: User = Depends(current_user)):
    """Every change to one option code across all of a model's comparisons."""
    if session.get(VehicleModel, model_id) is None:
        raise HTTPException(404, "no such model")
    rows = session.execute(
        select(ChangeEventRow, Comparison).join(Comparison)
        .where(Comparison.model_id == model_id, ChangeEventRow.code == code)
        .order_by(Comparison.id)).all()
    return [{"comparison_id": c.id, "old": c.old_model_year.year, "new": c.new_model_year.year,
             "eid": r.eid, "kind": r.kind, "sheet": r.sheet, "detail": r.detail}
            for r, c in rows]
