"""The judgments Jev is asked to make -- and only those.

Everything deterministic stays in code.  Asking the model a fact code already
knows converts certainty into probability, so there is no question here about
whether two RPO codes are equal, what kind of availability transition occurred,
or which trims changed.

Criteria levels describe concrete situations and stand on their own, and no
question's wording presupposes another's answer: questions in a request are
answered in parallel and cannot see each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..classify import ChangeEvent
from ..model.canonical import Constraint, Row
from ..rules.context import DEFAULT_CONTEXT, PromptContext
from ..rules.default_gm import DEFAULT_GM
from ..rules.model import JudgeRules, QuestionTemplate
from .client import Request

# The wording of every question is ruleset data (``rules.JudgeRules``), written
# as templates over the comparison's years and manufacturer.  Which question is
# asked about what, and how its answer is routed, stays here.
#
# The rendered wording is part of every judgment cache key, so rendering must
# be exact: the GM ruleset rendered for 2026 -> 2027 reproduces the original
# constants character for character (``tests/test_parity.py``).


def render_question(template: QuestionTemplate, ctx: PromptContext,
                    names: dict[str, str]) -> dict[str, Any]:
    q: dict[str, Any] = {
        "type": template.type,
        "instructions": ctx.render(template.instructions, **names),
    }
    if isinstance(template.criteria, dict):
        q["criteria"] = {k: ctx.render(v, **names) for k, v in template.criteria.items()}
    elif template.criteria:
        q["criteria"] = [ctx.render(v, **names) for v in template.criteria]
    return q


@dataclass(frozen=True, slots=True)
class QuestionSet:
    """A ruleset's questions rendered for one comparison."""

    questions: dict[str, dict[str, Any]]
    pair_old: str
    pair_new: str
    cond_old: str
    cond_new: str
    legend: dict[str, str]
    alignment_outcomes: tuple[str, ...]
    constraint_outcomes: tuple[str, ...]
    confidence_floor: float

    def __getitem__(self, qid: str) -> dict[str, Any]:
        return self.questions[qid]


def build_questions(judge: JudgeRules | None = None,
                    ctx: PromptContext | None = None) -> QuestionSet:
    judge = judge or DEFAULT_GM.judge
    ctx = ctx or DEFAULT_CONTEXT
    keys = judge.state_keys
    names = {
        "pair_old": ctx.render(keys.pair_old), "pair_new": ctx.render(keys.pair_new),
        "cond_old": ctx.render(keys.cond_old), "cond_new": ctx.render(keys.cond_new),
    }
    return QuestionSet(
        questions={qid: render_question(t, ctx, names) for qid, t in judge.questions.items()},
        legend=dict(judge.token_legend),
        alignment_outcomes=tuple(judge.alignment_outcomes),
        constraint_outcomes=tuple(judge.constraint_outcomes),
        confidence_floor=judge.confidence_floor,
        **names,
    )


#: The GM questions for 2026 -> 2027, under their original names.
DEFAULT_QUESTIONS = build_questions()


# --------------------------------------------------------------------------
# A. Identity -- asked only about rows the deterministic tiers could not pair.
# --------------------------------------------------------------------------

ALIGNMENT = DEFAULT_QUESTIONS["alignment"]
SUCCESSION = DEFAULT_QUESTIONS["succession"]
ABSORBED = DEFAULT_QUESTIONS["absorbed"]

#: Score levels map to outcomes by nearest-level rounding, so the boundaries
#: come from the level wording rather than a fitted threshold.
ALIGNMENT_OUTCOMES = DEFAULT_QUESTIONS.alignment_outcomes

#: Below this, even a confident-looking pairing goes to the review queue.
CONFIDENCE_FLOOR = DEFAULT_QUESTIONS.confidence_floor


def route_alignment(value: float, confidence: float | None, *,
                    outcomes: tuple[str, ...] = ALIGNMENT_OUTCOMES,
                    floor: float = CONFIDENCE_FLOOR) -> str:
    outcome = outcomes[min(int(value + 0.5), len(outcomes) - 1)]
    # The last outcome is the positive one ("pair"); it must also be confident.
    if outcome == outcomes[-1] and (confidence or 0.0) < floor:
        return outcomes[1]
    return outcome


def pairing_request(key: str, row_a: Row, row_b: Row,
                    qs: QuestionSet | None = None) -> Request:
    qs = qs or DEFAULT_QUESTIONS

    def describe(row: Row) -> dict[str, Any]:
        return {
            "sheet": row.sheet,
            "section": " / ".join(row.section_path) or None,
            "option_code": row.code,
            "description": row.display_description,
        }

    return Request(
        key=key,
        state={qs.pair_old: describe(row_a), qs.pair_new: describe(row_b)},
        questions={
            "alignment": qs["alignment"],
            "succession": qs["succession"],
            "absorbed": qs["absorbed"],
        },
    )


# --------------------------------------------------------------------------
# B. Rewritten constraints -- asked only where a clause kept its relation and
#    its cited codes but changed its prose, so code cannot tell whether the
#    restriction moved.
# --------------------------------------------------------------------------

CONSTRAINT_DIRECTION = DEFAULT_QUESTIONS["direction"]

CONSTRAINT_OUTCOMES = DEFAULT_QUESTIONS.constraint_outcomes


def route_constraint(value: float, *,
                     outcomes: tuple[str, ...] = CONSTRAINT_OUTCOMES) -> str:
    return outcomes[min(int(value + 0.5), len(outcomes) - 1)]


def constraint_request(key: str, old: Constraint, new: Constraint, *,
                       trim: str, code: str | None, description: str,
                       qs: QuestionSet | None = None) -> Request:
    qs = qs or DEFAULT_QUESTIONS
    return Request(
        key=key,
        state={
            "option_code": code,
            "option": description,
            "trim_level": trim,
            "relation": old.relation,
            "referenced_options": sorted(old.referenced_codes) or None,
            qs.cond_old: old.raw,
            qs.cond_new: new.raw,
        },
        questions={"direction": qs["direction"]},
    )


# --------------------------------------------------------------------------
# C & D. Description typology and the two materiality lenses, asked together
#        about each change event.
# --------------------------------------------------------------------------

DESCRIPTION_KIND = DEFAULT_QUESTIONS["description_kind"]
ORDER_RISK = DEFAULT_QUESTIONS["order_risk"]
CONTENT_CHURN = DEFAULT_QUESTIONS["content_churn"]


def event_state(event: ChangeEvent, legend: dict[str, str] | None = None) -> dict[str, Any]:
    """Serialize the deterministic delta for the model to judge.

    The model is given what code already worked out -- which trims moved, in
    which direction, and which conditions were added or dropped -- so it judges
    consequence rather than re-deriving mechanics.
    """
    moves = [
        {
            "trim": d.trim,
            "from": d.token_a or "(none)",
            "to": d.token_b or "(none)",
            "direction": d.direction.replace("_", " "),
            "conditions_added": [c.raw for c in d.constraints.added] or None,
            "conditions_dropped": [c.raw for c in d.constraints.removed] or None,
        }
        for d in event.deltas if d.changed
    ]
    return {
        "change": {
            "kind": event.kind.replace("_", " "),
            "option_code": event.code,
            "option": event.description,
            "worksheets": [event.sheet, *event.also_on],
            "summary": event.detail or None,
            "trims_affected": moves or None,
        },
        "legend": dict(legend if legend is not None else DEFAULT_QUESTIONS.legend),
    }


def materiality_request(key: str, event: ChangeEvent,
                        qs: QuestionSet | None = None) -> Request:
    qs = qs or DEFAULT_QUESTIONS
    questions: dict[str, Any] = {
        "order_risk": qs["order_risk"],
        "content_churn": qs["content_churn"],
    }
    # Only worth asking where code genuinely cannot tell marketing copy from a
    # spec correction.
    if event.kind == "description":
        questions["description_kind"] = qs["description_kind"]
    return Request(key=key, state=event_state(event, qs.legend), questions=questions)
