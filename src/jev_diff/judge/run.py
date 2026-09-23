"""Run the judgments and compose them into lenses.

Two passes are unavoidable: classification depends on pairing, and questions
within one request cannot see each other.  Everything inside a pass is fanned
out concurrently.

Raw judgments are persisted exactly as returned and never collapsed into a
single severity here.  Banding and weighting happen at read time, which is what
makes re-ranking free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from rapidfuzz import fuzz

from ..align.pairing import BookAlignment, Unpaired
from ..classify import ChangeEvent
from .cache import JudgmentCache
from .client import Reply, Request, ask_many
from .questions import (
    CONFIDENCE_FLOOR,
    constraint_request,
    materiality_request,
    pairing_request,
    route_alignment,
    route_constraint,
)

#: Below this description similarity two rows are not even worth asking about.
SIMILARITY_FLOOR = 50.0


@dataclass(slots=True)
class Lens:
    name: str
    weights: dict[str, float]
    bands: dict[str, float] = field(
        default_factory=lambda: {"material": 1.5, "review": 0.5}
    )
    confidence_floor: float = CONFIDENCE_FLOOR


DEFAULT_LENSES = {
    "ordering": Lens("ordering", {"order_risk": 1.0, "content_churn": 0.0}),
    "content": Lens("content", {"order_risk": 0.2, "content_churn": 1.0}),
}


def _split_requests(requests: list[Request], cache: JudgmentCache
                    ) -> tuple[list[Request], dict[str, dict[str, Any]]]:
    """Drop questions already answered for this exact state and wording.

    A request whose every question is cached is never sent at all, which is what
    makes re-running after a rendering change cost nothing.
    """
    to_send: list[Request] = []
    cached: dict[str, dict[str, Any]] = {}
    for request in requests:
        outstanding = {}
        for qid, question in request.questions.items():
            hit = cache.get(request.state, qid, question)
            if hit is None:
                outstanding[qid] = question
            else:
                cached.setdefault(request.key, {})[qid] = hit
        if outstanding:
            to_send.append(Request(request.key, request.state, outstanding))
    return to_send, cached


def _dispatch(requests: list[Request], cache: JudgmentCache, *, workers: int = 6
              ) -> tuple[dict[str, dict[str, Any]], list[str], dict[str, int]]:
    to_send, answers = _split_requests(requests, cache)
    errors: list[str] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0}
    if to_send:
        by_key = {r.key: r for r in to_send}
        for reply in ask_many(to_send, workers=workers):
            usage["requests"] += 1
            for field_name in ("input_tokens", "output_tokens"):
                usage[field_name] += reply.usage.get(field_name, 0)
            if reply.error:
                errors.append(f"{reply.key}: {reply.error}")
                continue
            request = by_key[reply.key]
            for qid, answer in reply.answers.items():
                cache.put(request.state, qid, request.questions[qid], answer)
                answers.setdefault(reply.key, {})[qid] = answer
    return answers, errors, usage


def candidate_pairs(alignment: BookAlignment) -> list[tuple[Unpaired, Unpaired]]:
    """Fuzzy candidates among the rows the deterministic tiers left over.

    Drawn from the whole residue, not just the rows the dictionary could not
    dispose of.  A row whose code the dictionary places on a new sheet and a
    code-less row on that same sheet can still be one feature -- "Sill plates,
    bright front and rear doors with GMC logo" became "(C3Q) Sill plates,
    bright, front and rear doors" -- and restricting candidates to the review
    bucket would put those two in different buckets and never compare them.

    Restricted to the same sheet: most option codes are listed on several
    sheets, so cross-sheet candidates would manufacture phantom matches.
    """
    out: list[tuple[Unpaired, Unpaired]] = []
    olds = [u for u in alignment.residue if u.year == "a"]
    news = [u for u in alignment.residue if u.year == "b"]
    for old in olds:
        for new in news:
            if old.sheet != new.sheet:
                continue
            similarity = fuzz.token_set_ratio(
                old.row.description, new.row.description
            )
            if similarity >= SIMILARITY_FLOOR:
                out.append((old, new))
    return out


@dataclass(slots=True)
class Judgments:
    pairings: dict[str, dict[str, Any]] = field(default_factory=dict)
    constraints: dict[str, dict[str, Any]] = field(default_factory=dict)
    materiality: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    cache_hits: int = 0
    cache_misses: int = 0


def event_key(index: int, event: ChangeEvent) -> str:
    return f"{index}:{event.kind}:{event.sheet}:{event.code or '-'}"


def run(alignment: BookAlignment, events: list[ChangeEvent],
        cache: JudgmentCache, *, workers: int = 6) -> Judgments:
    result = Judgments()
    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0}

    # Pass 1 -- identity for the residue, and direction for rewritten clauses.
    requests: list[Request] = []
    pair_index: dict[str, tuple[Unpaired, Unpaired]] = {}
    for old, new in candidate_pairs(alignment):
        key = f"pair:{old.sheet}:{old.row.description[:32]}->{new.row.description[:32]}"
        pair_index[key] = (old, new)
        requests.append(pairing_request(key, old.row, new.row))

    constraint_index: dict[str, tuple[ChangeEvent, str]] = {}
    for i, event in enumerate(events):
        for delta in event.deltas:
            for old_c, new_c in delta.constraints.needs_judgment:
                key = f"cond:{i}:{event.code}:{delta.axis_id}:{old_c.relation}"
                constraint_index[key] = (event, delta.trim)
                requests.append(constraint_request(
                    key, old_c, new_c, trim=delta.trim, code=event.code,
                    description=event.description,
                ))

    answers, errors, used = _dispatch(requests, cache, workers=workers)
    result.errors.extend(errors)
    for k in usage:
        usage[k] += used[k]

    for key, got in answers.items():
        if key.startswith("pair:"):
            alignment_answer = got.get("alignment", {})
            result.pairings[key] = {
                **got,
                "outcome": route_alignment(
                    alignment_answer.get("score", 0.0),
                    alignment_answer.get("confidence"),
                ),
            }
        elif key.startswith("cond:"):
            result.constraints[key] = {
                **got,
                "outcome": route_constraint(got.get("direction", {}).get("score", 1.0)),
            }

    # Pass 2 -- materiality over every event.
    material_requests = [
        materiality_request(event_key(i, event), event)
        for i, event in enumerate(events)
    ]
    answers, errors, used = _dispatch(material_requests, cache, workers=workers)
    result.errors.extend(errors)
    for k in usage:
        usage[k] += used[k]
    result.materiality = answers

    for i, event in enumerate(events):
        event.judgments = dict(answers.get(event_key(i, event), {}))

    result.usage = usage
    result.cache_hits, result.cache_misses = cache.hits, cache.misses
    cache.save()
    return result


def lens_value(event: ChangeEvent, lens: Lens) -> float:
    """Weighted combination of the raw scores.

    Changing a weight is arithmetic over judgments already stored, so it never
    re-runs inference.
    """
    total = 0.0
    for qid, weight in lens.weights.items():
        answer = event.judgments.get(qid) or {}
        total += weight * float(answer.get("score", 0.0))
    divisor = sum(abs(w) for w in lens.weights.values()) or 1.0
    return total / divisor


def band(event: ChangeEvent, lens: Lens) -> str:
    """Three bands, with uncertainty routed to review rather than guessed at."""
    if not event.judgments:
        return "unjudged"
    value = lens_value(event, lens)
    confidences = [
        (event.judgments.get(q) or {}).get("confidence")
        for q in lens.weights
        if lens.weights[q] > 0
    ]
    confidences = [c for c in confidences if c is not None]
    if value >= lens.bands["material"]:
        if confidences and min(confidences) < lens.confidence_floor:
            return "review"
        return "material"
    if value >= lens.bands["review"]:
        return "review"
    return "cosmetic"
