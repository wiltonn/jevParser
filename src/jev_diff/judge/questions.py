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

from typing import Any

from ..classify import ChangeEvent
from ..model.canonical import Constraint, Row
from .client import Request, choice, noul, score

# --------------------------------------------------------------------------
# A. Identity -- asked only about rows the deterministic tiers could not pair.
# --------------------------------------------------------------------------

ALIGNMENT = score(
    instructions=(
        "Two rows from the same worksheet of a GM vehicle order guide, one from "
        "the 2026 model year (`row_2026`) and one from 2027 (`row_2027`). How do "
        "they relate as entries in the order guide?"
    ),
    criteria=[
        "The rows describe unrelated equipment: a buyer reading one would not "
        "expect the other. For example one is a seating package and the other "
        "is a wheel.",
        "The rows describe equipment in the same subsystem, and one may be a "
        "successor, a variant, a renamed version, or a package that absorbed "
        "the other. A reader could reasonably argue either that these are one "
        "entry or two.",
        "The rows are the same order-guide entry for the same equipment. Any "
        "difference in wording is how the same equipment was described, not "
        "different equipment.",
    ],
)

SUCCESSION = noul(
    "`row_2026` names equipment that is no longer offered, and `row_2027` names "
    "the equipment that replaced it in the same position in the lineup."
)

ABSORBED = noul(
    "The equipment listed in `row_2026` is now bundled inside the package "
    "described in `row_2027` rather than being offered on its own."
)

#: Score levels map to outcomes by nearest-level rounding, so the boundaries
#: come from the level wording rather than a fitted threshold.
ALIGNMENT_OUTCOMES = ("unlink", "review", "pair")

#: Below this, even a confident-looking pairing goes to the review queue.
CONFIDENCE_FLOOR = 0.75


def route_alignment(value: float, confidence: float | None) -> str:
    outcome = ALIGNMENT_OUTCOMES[min(int(value + 0.5), len(ALIGNMENT_OUTCOMES) - 1)]
    if outcome == "pair" and (confidence or 0.0) < CONFIDENCE_FLOOR:
        return "review"
    return outcome


def pairing_request(key: str, row_a: Row, row_b: Row) -> Request:
    def describe(row: Row) -> dict[str, Any]:
        return {
            "sheet": row.sheet,
            "section": " / ".join(row.section_path) or None,
            "option_code": row.code,
            "description": row.display_description,
        }

    return Request(
        key=key,
        state={"row_2026": describe(row_a), "row_2027": describe(row_b)},
        questions={
            "alignment": ALIGNMENT,
            "succession": SUCCESSION,
            "absorbed": ABSORBED,
        },
    )


# --------------------------------------------------------------------------
# B. Rewritten constraints -- asked only where a clause kept its relation and
#    its cited codes but changed its prose, so code cannot tell whether the
#    restriction moved.
# --------------------------------------------------------------------------

CONSTRAINT_DIRECTION = score(
    instructions=(
        "`condition_2026` and `condition_2027` are the ordering constraints "
        "attached to the same option for the same trim level. Considering which "
        "vehicle configurations can be ordered, how does the 2027 constraint "
        "compare?"
    ),
    criteria=[
        "Configurations that could be ordered in 2026 can no longer be ordered "
        "in 2027: the option now requires something extra, or is now blocked by "
        "a combination that used to be allowed.",
        "The same configurations can be ordered under both constraints. The "
        "wording differs, or a referenced option was renamed or renumbered, but "
        "no configuration changes from orderable to un-orderable or the reverse.",
        "Configurations that could not be ordered in 2026 can be ordered in "
        "2027: a prerequisite was dropped, or an additional way to get the "
        "option was added.",
    ],
)

CONSTRAINT_OUTCOMES = ("tightened", "equivalent", "loosened")


def route_constraint(value: float) -> str:
    return CONSTRAINT_OUTCOMES[min(int(value + 0.5), len(CONSTRAINT_OUTCOMES) - 1)]


def constraint_request(key: str, old: Constraint, new: Constraint, *,
                       trim: str, code: str | None, description: str) -> Request:
    return Request(
        key=key,
        state={
            "option_code": code,
            "option": description,
            "trim_level": trim,
            "relation": old.relation,
            "referenced_options": sorted(old.referenced_codes) or None,
            "condition_2026": old.raw,
            "condition_2027": new.raw,
        },
        questions={"direction": CONSTRAINT_DIRECTION},
    )


# --------------------------------------------------------------------------
# C & D. Description typology and the two materiality lenses, asked together
#        about each change event.
# --------------------------------------------------------------------------

DESCRIPTION_KIND = choice(
    instructions=(
        "Only the wording describing this option changed between the model "
        "years. What kind of edit was it?"
    ),
    criteria={
        "marketing": "Promotional phrasing was added, dropped or rewritten, "
                     "with no change to what the equipment is or does.",
        "correction": "A specific factual detail was corrected or made more "
                      "precise, such as a measurement, a capacity or a name.",
        "capability": "The described capability of the equipment changed: it "
                      "now does more, less, or something different.",
        "housekeeping": "An editorial change with no reader-visible meaning, "
                        "such as punctuation, ordering or a reference marker.",
    },
)

ORDER_RISK = score(
    instructions=(
        "A dealer or fleet configurator has a saved 2026 build sheet and is "
        "re-ordering the same vehicle for 2027. What happens to that order "
        "because of `change`?"
    ),
    criteria=[
        # Level 0 must cover a *loosened* restriction as well as a pure wording
        # change. Tying it to "only the wording differs" left a lifted
        # constraint with no fitting level, and the answers came back split
        # between this level and the most severe one.
        "The same order succeeds and builds the same vehicle. Either nothing "
        "changed but the wording, or a restriction was lifted so configurations "
        "that were blocked before can now be ordered.",
        "The order still succeeds, but the vehicle differs from what was "
        "expected: an item is now bundled into a package, installed by the "
        "dealer rather than the factory, or fitted as standard rather than "
        "ordered separately.",
        "The order is rejected or has to be rebuilt: an option code no longer "
        "exists, has become reference-only, or now conflicts with another code "
        "on the order.",
    ],
)

CONTENT_CHURN = score(
    instructions=(
        "A marketing team maintains web pages, brochures and spec tables built "
        "from the 2026 order guide. What must they change because of `change`?"
    ),
    criteria=[
        "Nothing. No published sentence or table cell derived from this row "
        "changes.",
        "A published table cell or availability footnote changes, but no prose "
        "is rewritten.",
        "Published prose must be rewritten, or a feature must be added to or "
        "removed from a page, because the equipment or its described capability "
        "changed.",
    ],
)


def event_state(event: ChangeEvent) -> dict[str, Any]:
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
        "legend": {
            "S": "standard equipment",
            "A": "available as a separate order",
            "A/D": "available, dealer-installed",
            "■": "included in an equipment group",
            "--": "not available",
        },
    }


def materiality_request(key: str, event: ChangeEvent) -> Request:
    questions: dict[str, Any] = {
        "order_risk": ORDER_RISK,
        "content_churn": CONTENT_CHURN,
    }
    # Only worth asking where code genuinely cannot tell marketing copy from a
    # spec correction.
    if event.kind == "description":
        questions["description_kind"] = DESCRIPTION_KIND
    return Request(key=key, state=event_state(event), questions=questions)
