from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user, get_session
from ..config import get_settings
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


#: In request mode a job dies with its request, so anything silent for longer
#: than the host's request limit was killed there.
REQUEST_STALE_AFTER = timedelta(minutes=6)


@router.post("/jobs/drain")
def drain(_: User = Depends(current_user)):
    """Run the oldest queued job to completion inside this request.

    Only in ``job_mode = "request"``; with worker threads it returns at once.
    The web app calls this while it sees queued jobs.
    """
    if get_settings().job_mode != "request":
        return {"mode": "threads", "ran": 0}
    from ..jobs.queue import recover_stale
    from ..jobs.worker import run_pending

    recover_stale(REQUEST_STALE_AFTER)
    return {"mode": "request", "ran": run_pending(limit=1)}


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
