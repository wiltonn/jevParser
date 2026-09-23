"""The judgment cache as a table, keyed exactly as the JSON cache is.

Implements ``jev_diff.judge.cache.JudgmentStore``.  Answers arrive on the
client's worker threads, so ``put`` only buffers under a lock; ``save`` writes
them on the job's own thread.  Hit counts are flushed the same way.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from jev_diff.judge.cache import judgment_key

from ..db.models import Judgment, utcnow


class SqlJudgmentStore:
    def __init__(self, session_factory: sessionmaker):
        self._factory = session_factory
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[str, dict[str, Any]]] = {}
        self._hit_keys: list[str] = []
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(state: Any, question_id: str, question: dict[str, Any]) -> str:
        return judgment_key(state, question_id, question)

    def get(self, state, question_id, question):
        key = self.key(state, question_id, question)
        with self._lock:
            pending = self._pending.get(key)
        if pending is not None:
            self.hits += 1
            return pending[1]
        with self._factory() as session:
            row = session.get(Judgment, key)
            answer = row.answer if row is not None else None
        if answer is None:
            self.misses += 1
        else:
            self.hits += 1
            self._hit_keys.append(key)
        return answer

    def put(self, state, question_id, question, answer) -> None:
        key = self.key(state, question_id, question)
        with self._lock:
            self._pending[key] = (question_id, answer)

    def save(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, {}
            hits, self._hit_keys = self._hit_keys, []
        if not pending and not hits:
            return
        with self._factory() as session:
            for key, (qid, answer) in pending.items():
                if session.get(Judgment, key) is None:
                    session.add(Judgment(key=key, question_id=qid, answer=answer,
                                         source="api"))
            now = utcnow()
            for i in range(0, len(hits), 500):
                session.execute(
                    update(Judgment)
                    .where(Judgment.key.in_(hits[i:i + 500]))
                    .values(hit_count=Judgment.hit_count + 1, last_hit_at=now)
                )
            session.commit()


def import_json_cache(session: Session, source: str | Path | dict[str, Any]
                      ) -> tuple[int, int]:
    """Load a CLI ``judgments.json`` (path or parsed).  Returns ``(added, already_present)``."""
    data: dict[str, Any] = source if isinstance(source, dict) else json.loads(
        Path(source).read_text())
    if not all(isinstance(k, str) and isinstance(v, dict) for k, v in data.items()):
        raise ValueError("not a judgments cache: expected {key: answer}")
    existing: set[str] = set()
    keys = list(data)
    for i in range(0, len(keys), 500):
        existing |= set(session.scalars(select(Judgment.key).where(
            Judgment.key.in_(keys[i:i + 500]))))
    added = 0
    for key, answer in data.items():
        if key in existing:
            continue
        session.add(Judgment(key=key, answer=answer, source="json_import"))
        added += 1
    session.flush()
    return added, len(existing)


def export_json_cache(session: Session) -> dict[str, Any]:
    return {row.key: row.answer for row in session.scalars(select(Judgment))}
