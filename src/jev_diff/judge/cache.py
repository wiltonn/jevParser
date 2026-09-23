"""Content-addressed cache for judgments.

Keyed per **(state, question id, question definition)** rather than per request.
Requests still batch every question about one state together, because state is
paid for once per request; but lookups are per question, so editing one lens's
wording re-asks exactly that question and every other judgment is a cache hit.

Keyed on the whole request instead, changing a single word in one criterion
would re-bill an entire run -- the difference between tuning being pleasant and
being something you avoid.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any, Protocol

CACHE_VERSION = 1


class JudgmentStore(Protocol):
    """What the judgment runner needs from a cache.

    The JSON file below serves the CLI; the web app keeps the same keys in a
    database table, so either can answer for the other.
    """

    hits: int
    misses: int

    def get(self, state: Any, question_id: str,
            question: dict[str, Any]) -> dict[str, Any] | None: ...

    def put(self, state: Any, question_id: str, question: dict[str, Any],
            answer: dict[str, Any]) -> None: ...

    def save(self) -> None: ...


def judgment_key(state: Any, question_id: str, question: dict[str, Any]) -> str:
    """The content address of one question about one state."""
    return _digest(state, question_id, question)


def _digest(*parts: Any) -> str:
    h = hashlib.sha256()
    h.update(str(CACHE_VERSION).encode())
    for part in parts:
        h.update(json.dumps(part, sort_keys=True, default=str).encode())
        h.update(b"\x00")
    return h.hexdigest()[:32]


class JudgmentCache:
    def __init__(self, path: str | pathlib.Path):
        self.path = pathlib.Path(path)
        self._data: dict[str, Any] = {}
        self.hits = 0
        self.misses = 0
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}

    @staticmethod
    def key(state: Any, question_id: str, question: dict[str, Any]) -> str:
        return _digest(state, question_id, question)

    def get(self, state: Any, question_id: str,
            question: dict[str, Any]) -> dict[str, Any] | None:
        found = self._data.get(self.key(state, question_id, question))
        if found is None:
            self.misses += 1
        else:
            self.hits += 1
        return found

    def put(self, state: Any, question_id: str, question: dict[str, Any],
            answer: dict[str, Any]) -> None:
        self._data[self.key(state, question_id, question)] = answer

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=0, sort_keys=True))

    @property
    def size(self) -> int:
        return len(self._data)
