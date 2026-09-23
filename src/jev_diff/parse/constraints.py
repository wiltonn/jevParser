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

# An RPO code as GM cites it inside prose: exactly three chars, letters+digits,
# in parentheses.  "(SPZ)", "(L84)", "(5FF)".
_CODE_RE = re.compile(r"\(([0-9A-Z]{3})\)")

# Split on sentence boundaries without breaking 16.1" / 1.3" / 22" decimals.
# A boundary is ". " (or ".\n") NOT preceded by a digit, followed by something
# that starts a new clause: a capital, a digit-plus-quote, or "(".
_CLAUSE_RE = re.compile(r"(?<!\d)\.\s+(?=[A-Z(])")

# Ranked longest-intent-first.  The first match wins, so more specific patterns
# ("not available at this time") precede more general ones ("not available with").
#
# Classification runs in two stages.  Stage 1 anchors at the start of the clause,
# which is precise.  Stage 2 searches anywhere, because GM often leads with a
# parenthesised code or a time qualifier before stating the relation --
# "(S4X) Black mirror caps, LPO not available when ..." or "After initial 3-year
# period, requires paid OnStar plan".
_PATTERNS: tuple[tuple[str, str], ...] = (
    ("discontinued",       r"^no longer available\b"),
    ("not_available_now",  r"^not available at this time\b"),
    ("not_available_with", r"^not available (?:with|when|on|for)\b"),
    ("order_type_only",    r"^available on the following order types\b"),
    ("included_only_with", r"^(?:included and only available|required and only available|"
                           r"available only|only available|included on orders)\b"),
    ("requires",           r"^(?:requires|required with|required on|require|"
                           r"must be ordered)\b"),
    ("standard_with",      r"^standard (?:with|on)\b"),
    ("excludes_content",   r"^does not include\b"),
    ("included_with",      r"^(?:included with|included in|includes|also includes|"
                           r"enables and includes|enables)\b"),
    ("deleted_when",       r"^(?:deleted when|deletes|removes|removed when|removed with|"
                           r"will delete)\b"),
    ("replaced_by",        r"^replaced (?:by|with)\b"),
    ("drivetrain_only",    r"^(?:2wd|4wd)\b"),
    ("production_timing",  r"^(?:beginning|late availability|available beginning|effective)\b"),
    ("upgradeable_to",     r"^(?:upgradeable|upgradable)\b"),
    ("boilerplate",        r"^(?:always |terms and limitations|see dealer|important:|"
                           r"message and data|service |for more|visit |subject to|do not use|"
                           r"children are safer|read the vehicle|go to |available on select|"
                           r"content varies|all fees|standard for the first|after the trial|"
                           r"info at |not all vehicles|onstar |see |services vary|"
                           r"terms apply|plan is non-|some features require|siriusxm|"
                           r"safety services|trial subscription|will stop at)"),
)

# Stage 2: unanchored, same precedence order.  Deliberately narrower phrases
# than stage 1 so a mid-clause match is still unambiguous.
_FALLBACK: tuple[tuple[str, str], ...] = (
    ("substitutable",      r"\bcan be substituted\b"),
    # "Not available as a free-flow with ...", "Not available separately with ..."
    ("not_available_with", r"\bnot (?:available|included)\b[^.]{0,40}?\b(?:with|when|on|for)\b"),
    ("included_only_with", r"\b(?:included and only available|only available with)\b"),
    # "When (ATN) second row power bucket seats are ordered, includes (DCH) ..."
    ("included_with",      r"^when\b[^.]*?\bincludes\b"),
    ("requires",           r"\b(?:requires|must be purchased|must be ordered)\b"),
    ("deleted_when",       r"\bdeleted when\b"),
    ("replaced_by",        r"\breplaced (?:by|with)\b"),
    ("descriptive",        r"-specific\b|\bbody color\b|\bgloss black\b|^note that\b|"
                           r"^allows a\b|\bwill come with\b"),
    ("boilerplate",        r"\bsubject to change\b|\bterms and limitations\b|"
                           r"\bpay attention while driving\b|\bvaries by subscription\b"),
)

_COMPILED = tuple((name, re.compile(pat)) for name, pat in _PATTERNS)
_COMPILED_FALLBACK = tuple((name, re.compile(pat)) for name, pat in _FALLBACK)

#: Relations that restrict or permit an order.  Only these matter to the
#: ordering lens; the rest are prose that matters only to the content lens.
ORDERING_RELATIONS = frozenset({
    "requires", "not_available_with", "not_available_now", "included_only_with",
    "deleted_when", "replaced_by", "discontinued", "drivetrain_only",
    "standard_with", "substitutable", "upgradeable_to", "order_type_only",
    "excludes_content",
})

#: Relations that are prose, not constraints.  A delta confined to these can
#: never change what is orderable, so it is content-lens-only by construction.
PROSE_RELATIONS = frozenset({"boilerplate", "descriptive", "production_timing"})


def split_clauses(text: str) -> list[str]:
    """Split a footnote body into clauses, preserving decimal-inch measurements."""
    clean = re.sub(r"\s+", " ", (text or "")).strip()
    if not clean:
        return []
    parts = [p.strip().rstrip(".").strip() for p in _CLAUSE_RE.split(clean)]
    return [p for p in parts if p]


def classify(clause: str) -> Relation:
    """Stage 1 anchored, then stage 2 unanchored, then give up honestly."""
    low = clause.lower().strip()
    for name, rx in _COMPILED:
        if rx.match(low):
            return name  # type: ignore[return-value]
    for name, rx in _COMPILED_FALLBACK:
        if rx.search(low):
            return name  # type: ignore[return-value]
    return "unstructured"


def parse_clause(clause: str) -> Constraint:
    relation = classify(clause)
    codes = frozenset(_CODE_RE.findall(clause))
    # The residual is what remains once the relation phrase and the cited codes
    # are accounted for.  It is the ONLY part ever shown to a model.
    residual = _CODE_RE.sub("", clause)
    residual = re.sub(r"\s+", " ", residual).strip(" ,.;")
    return Constraint(
        relation=relation,
        referenced_codes=codes,
        residual_text=residual,
        raw=clause,
    )


def parse_footnote(text: str) -> tuple[Constraint, ...]:
    """A resolved footnote body -> one Constraint per clause."""
    return tuple(parse_clause(c) for c in split_clauses(text))


def cited_codes(text: str) -> frozenset[str]:
    """RPO codes cited in a description, used as a deterministic pairing feature."""
    return frozenset(_CODE_RE.findall(text or ""))
