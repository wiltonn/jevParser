"""Parse a resolved footnote into typed clauses.

This is the highest-leverage module in the parser.  Order-guide footnotes carry
their meaning in a small, closed relation vocabulary over parenthesised RPO
codes, so most year-over-year condition deltas can be classified — and given a
*direction* — with no model call.

Footnotes are parsed at **clause** granularity, not whole-text granularity,
because GM routinely appends or drops a single clause while leaving the rest
byte-identical.  Compare ``SFE``: the 2027 text is the 2026 text plus
``"Not available with (SPZ) Black wheel locks, LPO."``.  At clause level that is
one added ``not_available_with`` constraint; at text level it is an opaque
rewrite that would need a judgment call.
"""

from __future__ import annotations

import re

from ..model.canonical import Constraint, Relation
from ..rules.default_gm import DEFAULT_GM
from ..rules.model import ClauseRules

# The clause vocabulary -- the RPO code shape, the sentence splitter, the two
# ranked stages of relation patterns and the ordering/prose partition -- is
# ruleset data (``rules.ClauseRules``).  Stage 1 anchors at the start of the
# clause, which is precise.  Stage 2 searches anywhere, because GM often leads
# with a parenthesised code or a time qualifier before stating the relation --
# "(S4X) Black mirror caps, LPO not available when ..." or "After initial 3-year
# period, requires paid OnStar plan".  The names below are the GM defaults,
# kept for callers that predate rulesets.

_DEFAULT: ClauseRules = DEFAULT_GM.clauses

_CODE_RE = _DEFAULT.code_re
_CLAUSE_RE = _DEFAULT.clause_re
_PATTERNS: tuple[tuple[str, str], ...] = tuple(
    (p.relation, p.pattern) for p in _DEFAULT.anchored)
_FALLBACK: tuple[tuple[str, str], ...] = tuple(
    (p.relation, p.pattern) for p in _DEFAULT.fallback)

#: Relations that restrict or permit an order.  Only these matter to the
#: ordering lens; the rest are prose that matters only to the content lens.
ORDERING_RELATIONS = _DEFAULT.ordering

#: Relations that are prose, not constraints.  A delta confined to these can
#: never change what is orderable, so it is content-lens-only by construction.
PROSE_RELATIONS = _DEFAULT.prose


def split_clauses(text: str, clauses: ClauseRules | None = None) -> list[str]:
    """Split a footnote body into clauses, preserving decimal-inch measurements."""
    clause_re = (clauses or _DEFAULT).clause_re
    clean = re.sub(r"\s+", " ", (text or "")).strip()
    if not clean:
        return []
    parts = [p.strip().rstrip(".").strip() for p in clause_re.split(clean)]
    return [p for p in parts if p]


def classify(clause: str, clauses: ClauseRules | None = None) -> Relation:
    """Stage 1 anchored, then stage 2 unanchored, then give up honestly."""
    rules = clauses or _DEFAULT
    low = clause.lower().strip()
    for name, rx in rules.compiled_anchored:
        if rx.match(low):
            return name
    for name, rx in rules.compiled_fallback:
        if rx.search(low):
            return name
    return "unstructured"


def parse_clause(clause: str, clauses: ClauseRules | None = None) -> Constraint:
    rules = clauses or _DEFAULT
    relation = classify(clause, rules)
    codes = frozenset(rules.code_re.findall(clause))
    # The residual is what remains once the relation phrase and the cited codes
    # are accounted for.  It is the ONLY part ever shown to a model.
    residual = rules.code_re.sub("", clause)
    residual = re.sub(r"\s+", " ", residual).strip(" ,.;")
    return Constraint(
        relation=relation,
        referenced_codes=codes,
        residual_text=residual,
        raw=clause,
    )


def parse_footnote(text: str, clauses: ClauseRules | None = None) -> tuple[Constraint, ...]:
    """A resolved footnote body -> one Constraint per clause."""
    return tuple(parse_clause(c, clauses) for c in split_clauses(text, clauses))


def cited_codes(text: str, clauses: ClauseRules | None = None) -> frozenset[str]:
    """RPO codes cited in a description, used as a deterministic pairing feature."""
    return frozenset((clauses or _DEFAULT).code_re.findall(text or ""))
