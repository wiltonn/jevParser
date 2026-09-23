"""A job queue in the database.

Good enough for one machine and a handful of workers, and it needs nothing
else running.  Claiming is a conditional UPDATE, so two workers can never take
the same job; the same handler interface would sit behind RQ or Celery when
the app moves to a server.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..db.models import Job, utcnow

#: Set whenever a job is enqueued, so an idle worker wakes immediately.
wakeup = threading.Event()

STALE_AFTER = timedelta(minutes=2)


class JobCancelled(RuntimeError):
    pass


def enqueue(session: Session, type_: str, params: dict[str, Any] | None = None, *,
            comparison_id: int | None = None, document_id: int | None = None,
            user_id: int | None = None) -> Job:
    job = Job(type=type_, params=params or {}, comparison_id=comparison_id,
              document_id=document_id, created_by=user_id, message="queued")
    session.add(job)
    session.flush()
    wakeup.set()
    return job


def claim(worker_id: str) -> int | None:
    """Atomically take the oldest queued job.  Returns its id, or None."""
    with SessionLocal() as session:
        for _ in range(5):
            job_id = session.scalar(
                select(Job.id).where(Job.status == "queued").order_by(Job.id).limit(1))
            if job_id is None:
                return None
            now = utcnow()
            taken = session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "queued")
                .values(status="running", worker_id=worker_id, started_at=now,
                        heartbeat_at=now, message="started")
            ).rowcount
            session.commit()
            if taken:
                return job_id
        return None


def recover_stale() -> int:
    """Mark jobs left ``running`` by a process that died as failed."""
    cutoff = utcnow() - STALE_AFTER
    with SessionLocal() as session:
        jobs = session.scalars(select(Job).where(Job.status == "running")).all()
        count = 0
        for job in jobs:
            beat = job.heartbeat_at
            if beat is not None and beat.tzinfo is None:
                beat = beat.replace(tzinfo=cutoff.tzinfo)
            if beat is None or beat < cutoff:
                job.status, job.error = "failed", "interrupted: the server stopped mid-run"
                job.finished_at = utcnow()
                count += 1
        session.commit()
        return count


class JobContext:
    """What a handler gets: its job, progress reporting, and cancellation.

    Progress writes are throttled -- a judged run reports hundreds of steps --
    and the cancellation flag is re-read at most once a second.
    """

    def __init__(self, job_id: int, params: dict[str, Any], *, throttle: float = 0.25):
        self.job_id = job_id
        self.params = params
        self._throttle = throttle
        self._last_write = 0.0
        self._last_cancel_check = 0.0
        self._cancel = False
        self._lock = threading.Lock()

    def progress(self, stage: str, fraction: float, message: str | None = None,
                 force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_write < self._throttle:
                return
            self._last_write = now
        with SessionLocal() as session:
            session.execute(update(Job).where(Job.id == self.job_id).values(
                stage=stage, progress=round(max(0.0, min(1.0, fraction)), 4),
                message=message or stage.replace("_", " "), heartbeat_at=utcnow()))
            session.commit()

    def cancelled(self) -> bool:
        now = time.monotonic()
        if self._cancel or now - self._last_cancel_check < 1.0:
            return self._cancel
        self._last_cancel_check = now
        with SessionLocal() as session:
            self._cancel = bool(session.scalar(
                select(Job.cancel_requested).where(Job.id == self.job_id)))
        return self._cancel

    def check(self) -> None:
        if self.cancelled():
            raise JobCancelled("cancelled")
