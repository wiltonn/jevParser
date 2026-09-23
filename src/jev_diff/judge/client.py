"""Minimal TypeSafe System One client.

Deliberately a thin wrapper over the documented HTTP contract rather than the
SDK: the surface used here is four question shapes and one endpoint, and keeping
it dependency-free means the differ still runs (from cache, or with judgments
skipped) anywhere Python does.

The API key is read from the environment, never from anything the report or the
changeset touches.
"""

from __future__ import annotations

import json
import os
import pathlib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Iterable

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"


class JudgeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ApiConfig:
    """Where and as whom to ask.  ``key=None`` falls back to :func:`api_key`."""

    key: str | None = None
    endpoint: str = ENDPOINT
    model: str = MODEL
    timeout: float = 60.0


def api_key() -> str:
    """Environment first; fall back to Claude Code's user settings.

    The settings fallback exists because `env` entries there are applied at
    session start, so a freshly added key is not in the environment of an
    already-running session.
    """
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key:
        return key
    settings = pathlib.Path.home() / ".claude" / "settings.json"
    if settings.exists():
        try:
            found = json.loads(settings.read_text()).get("env", {}).get(
                "TYPESAFE_API_KEY", ""
            ).strip()
        except (json.JSONDecodeError, OSError):
            found = ""
        if found:
            return found
    raise JudgeError(
        "TYPESAFE_API_KEY is not set. Export it, or add it to the env block of "
        "~/.claude/settings.json."
    )


def noul(instructions: str, criteria: dict[str, str] | None = None) -> dict[str, Any]:
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if criteria:
        q["criteria"] = criteria
    return q


def choice(instructions: str, criteria: dict[str, str]) -> dict[str, Any]:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: str, criteria: list[str]) -> dict[str, Any]:
    return {"type": "score", "instructions": instructions, "criteria": criteria}


@dataclass(slots=True)
class Request:
    """One state plus every question asked about it.

    Questions in a request are answered in parallel and cannot see one another,
    so no question's wording may presuppose another's answer.
    """

    key: str                       # caller's identifier, echoed back on the reply
    state: Any
    questions: dict[str, dict[str, Any]]


@dataclass(slots=True)
class Reply:
    key: str
    answers: dict[str, Any]
    usage: dict[str, int]
    error: str | None = None

    def value(self, qid: str) -> float | str | None:
        """The headline value of an answer, whatever its primitive."""
        a = self.answers.get(qid)
        if not a:
            return None
        return a.get("score", a.get("noul", a.get("choice")))

    def confidence(self, qid: str) -> float | None:
        a = self.answers.get(qid) or {}
        return a.get("confidence")


def ask(request: Request, *, timeout: float = 60.0, key: str | None = None,
        endpoint: str = ENDPOINT, model: str = MODEL) -> Reply:
    payload = {
        "model": model,
        "state": request.state,
        "questions": request.questions,
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {key or api_key()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        return Reply(request.key, {}, {}, error=f"HTTP {exc.code}: {detail}")
    except Exception as exc:                       # network, timeout, bad JSON
        return Reply(request.key, {}, {}, error=f"{type(exc).__name__}: {exc}")
    return Reply(request.key, body.get("answers", {}), body.get("usage", {}))


def ask_many(requests: Iterable[Request], *, workers: int = 6,
             timeout: float = 60.0, api: ApiConfig | None = None,
             on_reply: Callable[[Reply], None] | None = None) -> list[Reply]:
    """Fan out over requests.

    The rate limit is far above anything this tool produces (a whole comparison
    is ~100 requests), so a small pool keeps latency down without needing
    backoff machinery.  ``on_reply`` is called as each reply lands, for progress.
    """
    batch = list(requests)
    if not batch:
        return []
    api = api or ApiConfig(timeout=timeout)
    key = api.key or api_key()                     # resolve once, not per thread

    def one(request: Request) -> Reply:
        reply = ask(request, timeout=api.timeout, key=key,
                    endpoint=api.endpoint, model=api.model)
        if on_reply is not None:
            on_reply(reply)
        return reply

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, batch))
