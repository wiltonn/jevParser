from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..auth import audit, current_user, get_session, require_admin
from ..db.models import AuditLog, Judgment, User
from ..services import settings_store
from ..services.judgment_store import export_json_cache, import_json_cache
from . import serialize as S

router = APIRouter(tags=["admin"])


class UserIn(BaseModel):
    email: str
    display_name: str
    role: str = "analyst"


class UserPatch(BaseModel):
    display_name: str | None = None
    role: str | None = None
    is_active: bool | None = None


@router.get("/me")
def me(user: User = Depends(current_user)):
    return S.user(user)


@router.get("/users")
def users(session: Session = Depends(get_session, scope="function"), _: User = Depends(require_admin)):
    return [S.user(u) for u in session.scalars(select(User).order_by(User.id))]


@router.post("/users", status_code=201)
def create_user(body: UserIn, session: Session = Depends(get_session, scope="function"),
                admin: User = Depends(require_admin)):
    if body.role not in ("admin", "analyst"):
        raise HTTPException(400, "role must be admin or analyst")
    if session.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, "that email is taken")
    u = User(email=body.email, display_name=body.display_name, role=body.role)
    session.add(u)
    session.flush()
    audit(session, admin, "create", "user", u.id, email=u.email, role=u.role)
    return S.user(u)


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserPatch, session: Session = Depends(get_session, scope="function"),
                admin: User = Depends(require_admin)):
    u = session.get(User, user_id)
    if u is None:
        raise HTTPException(404, "no such user")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("role") not in (None, "admin", "analyst"):
        raise HTTPException(400, "role must be admin or analyst")
    if u.id == admin.id and (changes.get("role") == "analyst" or changes.get("is_active") is False):
        raise HTTPException(400, "you cannot demote or deactivate yourself")
    for k, v in changes.items():
        setattr(u, k, v)
    audit(session, admin, "update", "user", u.id, **changes)
    return S.user(u)


@router.get("/settings")
def get_settings(session: Session = Depends(get_session, scope="function"), _: User = Depends(require_admin)):
    return settings_store.public_view(session)


@router.put("/settings")
def put_settings(body: dict, session: Session = Depends(get_session, scope="function"),
                 admin: User = Depends(require_admin)):
    for key, value in body.items():
        try:
            settings_store.put(session, key, value, admin.id)
        except KeyError as exc:
            raise HTTPException(400, str(exc))
    audit(session, admin, "update", "settings", None,
          keys=sorted(body), **{k: "***" for k in body if "key" in k})
    return settings_store.public_view(session)


@router.post("/settings/typesafe/test")
def test_typesafe(session: Session = Depends(get_session, scope="function"), _: User = Depends(require_admin)):
    """One tiny request, to check the key and endpoint before a long run."""
    from jev_diff.judge.client import JudgeError, Request, ask, noul

    api = settings_store.api_config(session)
    try:
        from jev_diff.judge.client import api_key

        key = api.key or api_key()
    except JudgeError as exc:
        return {"ok": False, "error": str(exc)}
    reply = ask(Request("probe", {"x": 1}, {"q": noul("`x` is a positive number.")}),
                key=key, endpoint=api.endpoint, model=api.model, timeout=20)
    return {"ok": reply.error is None, "error": reply.error, "usage": reply.usage}


@router.get("/judgments/stats")
def judgment_stats(session: Session = Depends(get_session, scope="function"), _: User = Depends(current_user)):
    by_q = session.execute(select(Judgment.question_id, func.count(), func.sum(Judgment.hit_count))
                           .group_by(Judgment.question_id)).all()
    by_source = dict(session.execute(select(Judgment.source, func.count())
                                     .group_by(Judgment.source)).all())
    return {"total": sum(n for _, n, _ in by_q), "by_source": by_source,
            "by_question": [{"question_id": q, "count": n, "hits": h or 0}
                            for q, n, h in by_q]}


@router.post("/judgments/import")
def import_judgments(file: UploadFile | None = File(default=None),
                     session: Session = Depends(get_session, scope="function"),
                     admin: User = Depends(require_admin)):
    if file is not None:
        try:
            data = json.loads(file.file.read())
        except json.JSONDecodeError as exc:
            raise HTTPException(400, f"not JSON: {exc}")
        added, present = import_json_cache(session, data)
    else:
        default = Path(".jev-cache/judgments.json")
        if not default.exists():
            raise HTTPException(400, "upload a judgments.json, or run from the repo root")
        added, present = import_json_cache(session, default)
    audit(session, admin, "import", "judgments", None, added=added)
    return {"added": added, "already_present": present}


@router.get("/judgments/export")
def export_judgments(session: Session = Depends(get_session, scope="function"), _: User = Depends(require_admin)):
    body = json.dumps(export_json_cache(session), indent=0, sort_keys=True)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="judgments.json"'})


@router.delete("/judgments")
def purge_judgments(question_id: str, session: Session = Depends(get_session, scope="function"),
                    admin: User = Depends(require_admin)):
    n = session.execute(delete(Judgment).where(Judgment.question_id == question_id)).rowcount
    audit(session, admin, "purge", "judgments", None, question_id=question_id, count=n)
    return {"deleted": n}


@router.get("/audit")
def audit_log(limit: int = 200, session: Session = Depends(get_session, scope="function"),
              _: User = Depends(require_admin)):
    rows = session.execute(select(AuditLog, User.email).outerjoin(User)
                           .order_by(AuditLog.id.desc()).limit(min(limit, 1000))).all()
    return [{"id": a.id, "at": S.ts(a.at), "user": email, "action": a.action,
             "entity_type": a.entity_type, "entity_id": a.entity_id, "detail": a.detail}
            for a, email in rows]
