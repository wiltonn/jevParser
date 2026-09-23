"""Who is asking.

A stub with the right seams: every route already depends on
``current_user``, and admin routes on ``require_admin``.  Locally that is the
seeded admin; the ``X-Jev-User`` header (an email) can impersonate another
seeded user for testing role behaviour.  A server deployment replaces
``current_user`` with real session or SSO validation and nothing else changes.
"""

from __future__ import annotations

from typing import Iterator

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .db.models import AuditLog, User


def get_session() -> Iterator[Session]:
    """One session per request, committed when the handler returns.

    Declared with ``scope="function"`` everywhere it is used, so the commit
    happens *before* the response is sent.  With the default request scope a
    client that refetches as soon as a POST returns can read the database
    before the POST has committed -- an upload would appear to vanish.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def current_user(session: Session = Depends(get_session, scope="function"),
                 x_jev_user: str | None = Header(default=None)) -> User:
    if x_jev_user:
        user = session.scalar(select(User).where(User.email == x_jev_user))
    else:
        user = session.scalar(select(User).where(User.role == "admin", User.is_active)
                              .order_by(User.id))
    if user is None or not user.is_active:
        raise HTTPException(401, "no such active user")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "administrators only")
    return user


def audit(session: Session, user: User | None, action: str, entity_type: str,
          entity_id: object = None, **detail) -> None:
    session.add(AuditLog(user_id=user.id if user else None, action=action,
                         entity_type=entity_type,
                         entity_id=None if entity_id is None else str(entity_id),
                         detail=detail or None))
