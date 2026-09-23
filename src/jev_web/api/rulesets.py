from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from jev_diff.judge.questions import build_questions
from jev_diff.parse.constraints import parse_footnote, split_clauses
from jev_diff.rules import PromptContext, RuleSet
from jev_diff.rules.model import ClauseRules, JudgeRules

from ..auth import audit, current_user, get_session, require_admin
from ..db.models import Document, Ruleset, RulesetVersion, User
from ..jobs.queue import enqueue
from ..services import rulesets as svc
from . import serialize as S

router = APIRouter(tags=["rulesets"])


class RulesetIn(BaseModel):
    name: str
    oem_id: int
    brand_id: int | None = None
    model_id: int | None = None
    year_from: int | None = None
    year_to: int | None = None
    format: str = "xlsx"
    priority: int = 0
    description: str | None = None
    clone_from_version_id: int | None = None


class RulesetPatch(BaseModel):
    name: str | None = None
    brand_id: int | None = None
    model_id: int | None = None
    year_from: int | None = None
    year_to: int | None = None
    format: str | None = None
    priority: int | None = None
    description: str | None = None


class DraftIn(BaseModel):
    body: dict
    notes: str | None = None


class TestIn(BaseModel):
    document_id: int
    pair_document_id: int | None = None


class ProbeIn(BaseModel):
    text: str
    version_id: int | None = None
    clauses: dict | None = None


class RenderIn(BaseModel):
    judge: dict
    old_year: str = "2026"
    new_year: str = "2027"
    oem: str = "GM"
    brand: str = ""
    model: str = ""


def _rs(session: Session, rid: int) -> Ruleset:
    r = session.get(Ruleset, rid)
    if r is None:
        raise HTTPException(404, "no such ruleset")
    return r


def _v(session: Session, vid: int) -> RulesetVersion:
    v = session.get(RulesetVersion, vid)
    if v is None:
        raise HTTPException(404, "no such ruleset version")
    return v


@router.get("/rulesets")
def list_rulesets(session: Session = Depends(get_session, scope="function"), _: User = Depends(current_user)):
    return [S.ruleset(r) for r in session.scalars(select(Ruleset).order_by(Ruleset.oem_id,
                                                                            Ruleset.name))]


@router.get("/rules/schema")
def schema(_: User = Depends(current_user)):
    return RuleSet.model_json_schema()


@router.post("/rulesets", status_code=201)
def create(body: RulesetIn, session: Session = Depends(get_session, scope="function"),
           user: User = Depends(require_admin)):
    if body.format not in ("xlsx", "pdf", "any"):
        raise HTTPException(400, "format must be xlsx, pdf or any")
    source = None
    if body.clone_from_version_id:
        source = _v(session, body.clone_from_version_id)
        rules = svc.load_rules(source)
    else:
        from jev_diff.rules import default_rules

        rules = default_rules()
    fields = body.model_dump(exclude={"clone_from_version_id", "format"})
    r = svc.create_ruleset(session, **fields, rules=rules, fmt=body.format,
                           cloned_from=source, user_id=user.id)
    audit(session, user, "create", "ruleset", r.id, name=r.name,
          cloned_from=body.clone_from_version_id)
    return S.ruleset(r)


@router.post("/rulesets/import", status_code=201)
def import_ruleset(oem_id: int, name: str | None = None, file: UploadFile = File(...),
                   session: Session = Depends(get_session, scope="function"), user: User = Depends(require_admin)):
    try:
        rules = svc.validate_body(json.loads(file.file.read()))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"not a valid ruleset: {exc}")
    r = svc.create_ruleset(session, name=name or rules.name, oem_id=oem_id, rules=rules,
                           fmt=rules.format, user_id=user.id)
    audit(session, user, "import", "ruleset", r.id, name=r.name)
    return S.ruleset(r)


@router.get("/rulesets/{rid}")
def get_ruleset(rid: int, session: Session = Depends(get_session, scope="function"),
                _: User = Depends(current_user)):
    return S.ruleset(_rs(session, rid))


@router.patch("/rulesets/{rid}")
def update_ruleset(rid: int, body: RulesetPatch, session: Session = Depends(get_session, scope="function"),
                   user: User = Depends(require_admin)):
    r = _rs(session, rid)
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(r, key, value)
    if r.year_from and r.year_to and r.year_from > r.year_to:
        raise HTTPException(400, "year_from is after year_to")
    audit(session, user, "update", "ruleset", r.id, **changes)
    return S.ruleset(r)


@router.post("/rulesets/{rid}/draft")
def open_draft(rid: int, session: Session = Depends(get_session, scope="function"),
               user: User = Depends(require_admin)):
    v = svc.open_draft(session, _rs(session, rid), user.id)
    return S.ruleset_version(v, body=True)


@router.get("/ruleset-versions/{vid}")
def get_version(vid: int, session: Session = Depends(get_session, scope="function"),
                _: User = Depends(current_user)):
    v = _v(session, vid)
    return dict(S.ruleset_version(v, body=True), ruleset=S.ruleset(v.ruleset, versions=False))


@router.put("/ruleset-versions/{vid}")
def save_draft(vid: int, body: DraftIn, session: Session = Depends(get_session, scope="function"),
               user: User = Depends(require_admin)):
    v = _v(session, vid)
    errors = svc.validation_errors(body.body)
    if errors:
        return JSONResponse({"detail": "invalid ruleset", "errors": errors}, status_code=422)
    svc.save_draft(session, v, body.body, body.notes)
    audit(session, user, "save_draft", "ruleset_version", v.id, hash=v.content_hash)
    return S.ruleset_version(v, body=True)


@router.post("/ruleset-versions/validate")
def validate(body: DraftIn, _: User = Depends(current_user)):
    return {"errors": svc.validation_errors(body.body)}


@router.delete("/ruleset-versions/{vid}", status_code=204)
def discard_draft(vid: int, session: Session = Depends(get_session, scope="function"),
                  user: User = Depends(require_admin)):
    v = _v(session, vid)
    if v.status != "draft":
        raise HTTPException(409, "only a draft can be discarded")
    if len(v.ruleset.versions) == 1:
        raise HTTPException(409, "a ruleset's only version cannot be discarded")
    audit(session, user, "discard_draft", "ruleset_version", v.id)
    session.delete(v)


@router.post("/ruleset-versions/{vid}/publish")
def publish(vid: int, session: Session = Depends(get_session, scope="function"),
            user: User = Depends(require_admin)):
    v = svc.publish(session, _v(session, vid), user.id)
    audit(session, user, "publish", "ruleset_version", v.id, version=v.version)
    return S.ruleset_version(v)


@router.post("/ruleset-versions/{vid}/archive")
def archive(vid: int, session: Session = Depends(get_session, scope="function"),
            user: User = Depends(require_admin)):
    v = svc.archive(_v(session, vid))
    audit(session, user, "archive", "ruleset_version", v.id, version=v.version)
    return S.ruleset_version(v)


@router.get("/ruleset-versions/{vid}/diff")
def diff(vid: int, against: int | None = None, session: Session = Depends(get_session, scope="function"),
         _: User = Depends(current_user)):
    v = _v(session, vid)
    base = _v(session, against) if against else svc.latest_published(v.ruleset)
    if base is None:
        return {"against": None, "changes": []}
    return {"against": S.ruleset_version(base), "changes": svc.diff(base.body, v.body)}


@router.get("/ruleset-versions/{vid}/export")
def export(vid: int, session: Session = Depends(get_session, scope="function"),
           _: User = Depends(current_user)):
    v = _v(session, vid)
    text = svc.load_rules(v).to_json() + "\n"
    name = f"{v.ruleset.name} v{v.version}.json".replace("/", "-")
    return Response(text, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/ruleset-versions/{vid}/test", status_code=202)
def test(vid: int, body: TestIn, session: Session = Depends(get_session, scope="function"),
         user: User = Depends(require_admin)):
    v = _v(session, vid)
    for doc_id in filter(None, (body.document_id, body.pair_document_id)):
        if session.get(Document, doc_id) is None:
            raise HTTPException(404, f"no document {doc_id}")
    job = enqueue(session, "test_ruleset", {"ruleset_version_id": v.id,
                                            **body.model_dump()}, user_id=user.id)
    return S.job(job)


@router.post("/rules/clause-probe")
def clause_probe(body: ProbeIn, session: Session = Depends(get_session, scope="function"),
                 _: User = Depends(current_user)):
    """Classify footnote text with a version's (or an unsaved) clause rules.

    Run server-side because Python and JavaScript regular expressions differ.
    """
    if body.clauses is not None:
        try:
            clauses = ClauseRules.model_validate(body.clauses)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    elif body.version_id:
        clauses = svc.load_rules(_v(session, body.version_id)).clauses
    else:
        from jev_diff.rules import default_rules

        clauses = default_rules().clauses
    return [
        {"clause": text, "relation": c.relation, "codes": sorted(c.referenced_codes),
         "residual": c.residual_text,
         "ordering": c.relation in clauses.ordering,
         "prose": c.relation in clauses.prose}
        for text, c in zip(split_clauses(body.text, clauses), parse_footnote(body.text, clauses))
    ]


@router.post("/rules/render-questions")
def render_questions(body: RenderIn, _: User = Depends(current_user)):
    try:
        judge = JudgeRules.model_validate(body.judge)
        ctx = PromptContext(body.old_year, body.new_year, body.oem, body.brand, body.model)
        qs = build_questions(judge, ctx)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc))
    return {"questions": qs.questions,
            "state_keys": {"pair_old": qs.pair_old, "pair_new": qs.pair_new,
                           "cond_old": qs.cond_old, "cond_new": qs.cond_new}}
