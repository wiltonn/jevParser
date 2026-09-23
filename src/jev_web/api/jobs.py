from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user, get_session
from ..db.models import Job, User
from . import serialize as S

router = APIRouter(tags=["jobs"])


@router.get("/jobs")
def list_jobs(status: str | None = None, type: str | None = None, limit: int = 100,
              session: Session = Depends(get_session, scope="function"), _: User = Depends(current_user)):
    q = select(Job).order_by(Job.id.desc()).limit(min(limit, 500))
    if status:
        q = q.where(Job.status.in_(status.split(",")))
    if type:
        q = q.where(Job.type == type)
    return [S.job(j) for j in session.scalars(q)]


@router.get("/jobs/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session, scope="function"),
            _: User = Depends(current_user)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return S.job(job)


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: int, session: Session = Depends(get_session, scope="function"),
           _: User = Depends(current_user)):
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    if job.status == "queued":
        job.status, job.message = "cancelled", "cancelled before it started"
        if job.comparison_id and job.type == "run_comparison":
            from ..db.models import Comparison

            session.get(Comparison, job.comparison_id).status = "cancelled"
    elif job.status == "running":
        job.cancel_requested, job.message = True, "cancelling"
    return S.job(job)
