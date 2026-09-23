"""Worker threads that drain the job queue inside the web process."""

from __future__ import annotations

import logging
import os
import threading
import traceback

from sqlalchemy import update

from jev_diff.judge.run import Cancelled

from ..db import SessionLocal
from ..db.models import Comparison, Job, utcnow
from . import queue
from .handlers import HANDLERS

log = logging.getLogger("jev_web.worker")


def run_job(job_id: int) -> None:
    """Execute one claimed job to completion, recording the outcome."""
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        job_type, params = job.type, dict(job.params or {})
    ctx = queue.JobContext(job_id, params)
    status, error, result = "succeeded", None, None
    try:
        handler = HANDLERS[job_type]
        result = handler(ctx)
    except (queue.JobCancelled, Cancelled):
        status, error = "cancelled", None
    except Exception as exc:                          # recorded, never re-raised
        status, error = "failed", f"{type(exc).__name__}: {exc}"
        log.error("job %s failed\n%s", job_id, traceback.format_exc())
    with SessionLocal() as session:
        session.execute(update(Job).where(Job.id == job_id).values(
            status=status, error=error, result=result, finished_at=utcnow(),
            progress=1.0 if status == "succeeded" else Job.progress,
            message={"succeeded": "done", "cancelled": "cancelled",
                     "failed": "failed"}[status]))
        job = session.get(Job, job_id)
        if job.comparison_id and job.type == "run_comparison":
            comparison = session.get(Comparison, job.comparison_id)
            comparison.status = status
            comparison.error = error
            comparison.finished_at = utcnow()
        session.commit()


def run_pending(limit: int | None = None) -> int:
    """Drain the queue on the calling thread.  Used by tests and ``jev-web worker --once``."""
    done = 0
    while limit is None or done < limit:
        job_id = queue.claim(f"inline-{os.getpid()}")
        if job_id is None:
            break
        run_job(job_id)
        done += 1
    return done


class WorkerPool:
    def __init__(self, count: int):
        self.count = count
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        queue.recover_stale()
        for i in range(self.count):
            t = threading.Thread(target=self._loop, args=(f"w{os.getpid()}-{i}",),
                                 name=f"jev-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        queue.wakeup.set()
        for t in self._threads:
            t.join(timeout)

    def _loop(self, worker_id: str) -> None:
        while not self._stop.is_set():
            try:
                job_id = queue.claim(worker_id)
            except Exception:
                log.exception("claim failed")
                job_id = None
            if job_id is None:
                queue.wakeup.wait(1.0)
                queue.wakeup.clear()
                continue
            run_job(job_id)
