"""Type every change deterministically, before any model is consulted.

Availability transitions are a small closed lattice, and conditions are already
parsed into typed clauses, so direction and kind are computed here by rule.  Jev
is left only the judgments code genuinely cannot make: how much a change
matters, whether two unpaired rows are the same entry, and whether a genuinely
rewritten clause loosened or tightened.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz import fuzz

from .align.axes import AxisAlignment, align_axis
from .align.pairing import BookAlignment, Pair, Unpaired
from .model.canonical import Cell, Constraint, Provenance, Token, Workbook
from .parse.constraints import ORDERING_RELATIONS, PROSE_RELATIONS

#: The four ways a feature can be had.  Direction is computed between *classes*,
#: not raw tokens, because "A -> A/D" and "A -> S" are different in kind.
STANDARD, PACKAGE, OPTIONAL, UNAVAILABLE = "standard", "package", "optional", "unavailable"

_CLASS: dict[Token, str] = {
    Token.STANDARD: STANDARD,
    Token.INCLUDED: PACKAGE,
    Token.UPGRADEABLE: PACKAGE,
    Token.AVAILABLE: OPTIONAL,
    Token.AVAILABLE_ADI: OPTIONAL,
    Token.ADI: OPTIONAL,
    Token.NOT_AVAILABLE: UNAVAILABLE,
    Token.BLANK: UNAVAILABLE,
    Token.MULTI_MODEL: OPTIONAL,
    Token.CODE: OPTIONAL,
}

Direction = Literal[
    "unchanged",
    "content_added",        # was not available, now is
    "content_removed",      # was available, now is not
    "upgraded",             # easier to get (optional -> standard, package -> standard)
    "decontented",          # harder to get (standard -> optional/package)
    "package_shift",        # moved between separately-ordered and bundled
    "fulfillment_shift",    # same class, different token (A -> A/D)
    "constraint_only",      # token identical, conditions differ
    "incomparable",         # the trim exists in only one year
]

#: Directions that change what a dealer must do to get the feature.
ORDER_AFFECTING = frozenset({
    "content_added", "content_removed", "upgraded", "decontented",
    "package_shift", "fulfillment_shift", "constraint_only",
})


def _direction(a: Token, b: Token) -> Direction:
    ca, cb = _CLASS[a], _CLASS[b]
    if a == b:
        return "unchanged"
    if ca == cb:
        # A blank cell and "--" both mean the feature cannot be had, so GM
        # making the implicit explicit is cosmetic.  Within any other class the
        # token still carries meaning: "A" -> "A/D" moves a feature from
        # factory-ordered to dealer-installed, which changes how it is obtained.
        return "unchanged" if ca == UNAVAILABLE else "fulfillment_shift"
    if ca == UNAVAILABLE:
        return "content_added"
    if cb == UNAVAILABLE:
        return "content_removed"
    if cb == STANDARD:
        return "upgraded"
    if ca == STANDARD:
        return "decontented"
    return "package_shift"


@dataclass(slots=True)
class ConstraintDelta:
    """What changed among a cell's resolved conditions.

    Clauses are matched up by ``(relation, referenced_codes)`` before being
    called added or removed.  Without that step a reworded clause -- same
    relation, same cited codes, different prose -- appears as a removal plus an
    addition, and the differ reports a constraint change where GM only changed
    wording.  Those matched-but-different clauses land in ``rewritten``, and
    they are the only conditions that need a judgment call.
    """

    added: tuple[Constraint, ...] = ()
    removed: tuple[Constraint, ...] = ()
    rewritten: tuple[tuple[Constraint, Constraint], ...] = ()

    @property
    def any(self) -> bool:
        return bool(self.added or self.removed or self.rewritten)

    @property
    def order_affecting(self) -> bool:
        """Whether the change can alter what is orderable.

        A delta confined to prose relations (boilerplate, marketing copy,
        production timing) cannot, by construction.  Rewrites are excluded:
        whether a reworded clause changed the restriction is exactly the
        question this layer cannot answer, so it is left to the judgment layer
        rather than assumed either way.
        """
        return any(
            c.relation in ORDERING_RELATIONS
            for c in self.added + self.removed
        )

    @property
    def wording_only(self) -> bool:
        """Every difference is a rewrite of a clause with the same relation and
        the same cited codes -- so nothing was added or dropped."""
        return bool(self.rewritten) and not self.added and not self.removed

    @property
    def loosened(self) -> bool:
        """A restriction was dropped and none added: strictly easier to order."""
        return bool(self.removed) and not any(
            c.relation in ORDERING_RELATIONS for c in self.added
        ) and any(c.relation in ORDERING_RELATIONS for c in self.removed)

    @property
    def tightened(self) -> bool:
        return bool(self.added) and not any(
            c.relation in ORDERING_RELATIONS for c in self.removed
        ) and any(c.relation in ORDERING_RELATIONS for c in self.added)

    @property
    def needs_judgment(self) -> tuple[tuple[Constraint, Constraint], ...]:
        """Rewritten clauses whose relation can restrict an order.

        A reworded piece of boilerplate cannot change what is orderable, so it
        is not worth a model call; a reworded ``requires`` clause might be.
        """
        return tuple(
            (old, new) for old, new in self.rewritten
            if old.relation in ORDERING_RELATIONS
        )


def _shape(c: Constraint) -> tuple:
    """A clause's structural identity, ignoring its prose."""
    return (c.relation, tuple(sorted(c.referenced_codes)))


#: How alike two same-shape clauses must read before one is called a rewrite of
#: the other rather than an unrelated addition and removal.  Without this, the
#: many code-less boilerplate clauses on subscription rows all share one shape
#: and pair arbitrarily with each other.
REWRITE_SIMILARITY = 60.0


def _constraint_delta(a: Cell | None, b: Cell | None) -> ConstraintDelta:
    """Diff resolved clauses, so footnote renumbering vanishes.

    Two passes: exact clause equality first (which absorbs renumbering), then
    structural matching of the remainder, which separates genuine additions and
    removals from mere rewording.
    """
    ka = {c.key(): c for c in (a.conditions if a else ())}
    kb = {c.key(): c for c in (b.conditions if b else ())}
    left = [ka[k] for k in ka.keys() - kb.keys()]
    right = [kb[k] for k in kb.keys() - ka.keys()]

    # Sort before matching so the result does not depend on set iteration
    # order, which Python varies per process.
    left.sort(key=lambda c: (c.key(), c.raw))
    right.sort(key=lambda c: (c.key(), c.raw))

    rewritten: list[tuple[Constraint, Constraint]] = []
    for old in list(left):
        candidates = [
            (fuzz.token_set_ratio(old.residual_text, n.residual_text), i, n)
            for i, n in enumerate(right)
            if _shape(n) == _shape(old)
        ]
        best = max(candidates, default=None)
        if best is not None and best[0] >= REWRITE_SIMILARITY:
            left.remove(old)
            right.remove(best[2])
            rewritten.append((old, best[2]))

    # Sort deterministically.  These come from unordered set differences, and
    # Python randomizes string hashing per process, so leaving them unsorted
    # makes the judgment cache key differ between runs -- every judgment would
    # be re-billed on each invocation.
    by_key = lambda c: c.key()
    return ConstraintDelta(
        added=tuple(sorted(right, key=by_key)),
        removed=tuple(sorted(left, key=by_key)),
        rewritten=tuple(sorted(rewritten, key=lambda pair: pair[0].key())),
    )


@dataclass(slots=True)
class CellDelta:
    axis_id: str
    trim: str
    direction: Direction
    token_a: str
    token_b: str
    constraints: ConstraintDelta
    prov_a: Provenance | None
    prov_b: Provenance | None

    @property
    def changed(self) -> bool:
        return self.direction not in ("unchanged", "incomparable")


def cell_deltas(pair: Pair, axes: AxisAlignment) -> list[CellDelta]:
    """Compare a paired row's cells by axis identity, never by position."""
    out: list[CellDelta] = []
    for col_a, col_b in axes.pairs:
        a = pair.a.values.get(col_a.axis_id)
        b = pair.b.values.get(col_b.axis_id)
        if a is None and b is None:
            continue
        constraints = _constraint_delta(a, b)
        if a is None or b is None:
            direction: Direction = "incomparable"
        else:
            direction = _direction(a.token, b.token)
            if direction == "unchanged" and constraints.any:
                direction = "constraint_only"
        out.append(CellDelta(
            axis_id=col_b.axis_id,
            trim=col_b.display_name,
            direction=direction,
            token_a=a.token.value if a else "",
            token_b=b.token.value if b else "",
            constraints=constraints,
            prov_a=a.prov if a else None,
            prov_b=b.prov if b else None,
        ))
    # Columns present in only one year: a third state, not "unchanged".
    for col in axes.removed:
        cell = pair.a.values.get(col.axis_id)
        out.append(CellDelta(col.axis_id, col.display_name, "incomparable",
                             cell.token.value if cell else "", "",
                             ConstraintDelta(), cell.prov if cell else None, None))
    for col in axes.added:
        cell = pair.b.values.get(col.axis_id)
        out.append(CellDelta(col.axis_id, col.display_name, "incomparable",
                             "", cell.token.value if cell else "",
                             ConstraintDelta(), None, cell.prov if cell else None))
    return out


Kind = Literal[
    "availability",   # which trims get it, or how, changed
    "constraint",     # the conditions on it changed
    "description",    # only the wording changed
    "identity",       # the option code or its orderable status changed
    "membership",     # the row moved between sheets, or a duplicate listing went
    "row_added",
    "row_removed",
    "value",          # a spec or grid value changed
]


@dataclass(slots=True)
class ChangeEvent:
    kind: Kind
    sheet: str
    code: str | None
    description: str
    #: Other sheets carrying this identical change.  Most codes are listed on
    #: several sheets, so the same edit shows up repeatedly; consolidating keeps
    #: one finding per real change instead of one per listing.
    also_on: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()
    deltas: list[CellDelta] = field(default_factory=list)
    detail: str = ""
    tier: int | None = None
    disposition: str | None = None
    #: Filled in by the judgment layer; absent means "not yet judged".
    judgments: dict[str, object] = field(default_factory=dict)
    #: Stable address for deep links, assigned by :func:`consolidate`.
    eid: str = ""
    #: ``(year, sheet, rid)`` of every row that produced this event, so the
    #: sheets view can link a row to the change it caused and back again.
    #: The year matters: a retired or moved-off row exists only in the old
    #: workbook, and a link to it must not be resolved against the new one.
    row_ids: tuple[tuple[str, str, str], ...] = ()

    @property
    def changed_trims(self) -> list[str]:
        return [d.trim for d in self.deltas if d.changed]

    @property
    def order_affecting(self) -> bool:
        if self.kind in ("identity", "row_added", "row_removed"):
            return True
        if self.kind == "membership":
            return True
        return any(
            d.direction in ORDER_AFFECTING and
            (d.direction != "constraint_only" or d.constraints.order_affecting)
            for d in self.deltas
        )

    @property
    def needs_constraint_judgment(self) -> tuple[tuple[Constraint, Constraint], ...]:
        return tuple(
            pair for d in self.deltas for pair in d.constraints.needs_judgment
        )


def _describe(pair: Pair, deltas: list[CellDelta]) -> str:
    """Template one sentence per change.

    Jev returns typed judgments, never prose, so every human-readable line is
    built here from the deterministic delta.
    """
    bits: list[str] = []
    for d in (x for x in deltas if x.changed):
        if d.direction == "constraint_only":
            cd = d.constraints
            if cd.loosened:
                what = "; ".join(c.raw[:60] for c in cd.removed) or "a condition"
                bits.append(f"{d.trim}: condition dropped ({what})")
            elif cd.tightened:
                what = "; ".join(c.raw[:60] for c in cd.added) or "a condition"
                bits.append(f"{d.trim}: condition added ({what})")
            elif cd.wording_only:
                bits.append(f"{d.trim}: condition reworded "
                            f"({len(cd.rewritten)} clause(s), same relation and codes)")
            else:
                # Several things at once: say what moved, not just "changed".
                parts = []
                if cd.removed:
                    parts.append("-" + "; ".join(c.raw[:44] for c in cd.removed))
                if cd.added:
                    parts.append("+" + "; ".join(c.raw[:44] for c in cd.added))
                if cd.rewritten:
                    parts.append(f"~{len(cd.rewritten)} reworded")
                bits.append(f"{d.trim}: " + " ".join(parts))
        else:
            arrow = f"{d.token_a or '(none)'} -> {d.token_b or '(none)'}"
            bits.append(f"{d.trim}: {d.direction.replace('_', ' ')} ({arrow})")
    return "; ".join(bits)


def build_events(book_a: Workbook, book_b: Workbook,
                 alignment: BookAlignment) -> list[ChangeEvent]:
    """Turn an alignment into typed, deterministically-described change events."""
    events: list[ChangeEvent] = []

    for name, sheet_align in alignment.sheets.items():
        axes = align_axis(book_a.sheets[name].columns, book_b.sheets[name].columns)

        if axes.changed:
            # Reported once per sheet, never repeated on every row.
            detail = []
            if axes.added:
                detail.append("added " + ", ".join(c.display_name for c in axes.added))
            if axes.removed:
                detail.append("removed " + ", ".join(c.display_name for c in axes.removed))
            if axes.renamed:
                detail.append("renamed " + ", ".join(
                    f"{a.display_name} -> {b.display_name}" for a, b in axes.renamed))
            events.append(ChangeEvent(
                kind="identity", sheet=name, code=None,
                description="trim lineup", detail="; ".join(detail),
            ))

        for pair in sheet_align.pairs:
            deltas = cell_deltas(pair, axes)
            changed = [d for d in deltas if d.changed]

            if pair.code_column_swapped:
                events.append(ChangeEvent(
                    kind="identity", sheet=name, code=pair.b.code,
                    description=pair.b.display_description, deltas=deltas,
                    tier=pair.tier, row_ids=(("new", name, pair.b.rid),),
                    detail=(
                        f"orderable {pair.a.orderable_rpo or '(reference only)'} -> "
                        f"{pair.b.orderable_rpo or '(reference only)'}"
                    ),
                ))

            if changed:
                only_constraints = all(d.direction == "constraint_only" for d in changed)
                events.append(ChangeEvent(
                    kind="constraint" if only_constraints else "availability",
                    sheet=name, code=pair.b.code,
                    description=pair.b.display_description,
                    deltas=deltas, tier=pair.tier, row_ids=(("new", name, pair.b.rid),),
                    detail=_describe(pair, deltas),
                ))

            if pair.description_changed and not changed:
                events.append(ChangeEvent(
                    kind="description", sheet=name, code=pair.b.code,
                    description=pair.b.display_description, tier=pair.tier,
                    row_ids=(("new", name, pair.b.rid),),
                    detail=f"{pair.a.display_description!r} -> "
                           f"{pair.b.display_description!r}",
                ))

    for u in alignment.residue:
        kind: Kind
        if u.disposition in ("moved_off_sheet", "moved_onto_sheet",
                             "listing_removed", "listing_added"):
            kind = "membership"
        elif u.disposition == "retired" or (u.year == "a" and
                                            u.disposition == "needs_review"):
            kind = "row_removed"
        else:
            kind = "row_added"
        events.append(ChangeEvent(
            kind=kind, sheet=u.sheet, code=u.row.code,
            description=u.row.display_description,
            disposition=u.disposition,
            row_ids=(("old" if u.year == "a" else "new", u.sheet, u.row.rid),),
            detail=u.disposition.replace("_", " "),
        ))
    return events


def _signature(event: ChangeEvent) -> tuple:
    """Identity of a change independent of which sheet reported it."""
    return (event.kind, event.code, event.description, event.detail,
            event.disposition)


def _id_basis(event: ChangeEvent) -> str:
    """The inputs a permanent link may depend on.

    Deliberately narrower than :func:`_signature`, which includes ``detail`` --
    generated prose built from *truncated* condition text.  Rewording a sentence
    template would silently break every saved link, so no prose appears here.

    ``sheet`` is absent too: consolidation merges the same change across sheets,
    so an id that named one of them would not survive the merge.

    The axis fingerprint is what separates the two events a single pair can
    emit -- the ``identity`` event for an orderable/reference-only swap and the
    ``availability`` or ``constraint`` event for the same row -- without
    depending on wording.
    """
    axis = "|".join(
        f"{d.axis_id}:{d.direction}"
        for d in sorted(event.deltas, key=lambda d: d.axis_id)
    )
    return "\0".join((
        event.kind,
        event.code or "",
        re.sub(r"\s+", " ", event.description or "").strip().casefold(),
        event.disposition or "",
        axis,
    ))


def event_id(event: ChangeEvent) -> str:
    """An 8-character address for an event, stable across runs and rewordings."""
    return hashlib.blake2b(
        _id_basis(event).encode("utf-8"), digest_size=4
    ).hexdigest()


def consolidate(events: list[ChangeEvent]) -> list[ChangeEvent]:
    """Collapse the same change reported on several sheets into one event.

    77-79% of option codes are listed on more than one sheet, so an edit to a
    single feature surfaces two or three times.  The sheets are kept on the
    event, because "this changed on Interior and Standard Equipment" is useful,
    but the finding is counted once.
    """
    merged: dict[tuple, ChangeEvent] = {}
    for event in events:
        sig = _signature(event)
        first = merged.get(sig)
        if first is None:
            merged[sig] = event
            continue
        if event.sheet != first.sheet and event.sheet not in first.also_on:
            first.also_on = first.also_on + (event.sheet,)
        for ref in event.row_ids:
            if ref not in first.row_ids:
                first.row_ids = first.row_ids + (ref,)

    out = list(merged.values())
    seen: dict[str, ChangeEvent] = {}
    for event in out:
        event.eid = event_id(event)
        clash = seen.get(event.eid)
        if clash is not None:
            # Two cards sharing one address would make deep links land on the
            # wrong change.  Fail rather than emit it.
            raise ValueError(
                f"event id collision {event.eid}: "
                f"{clash.kind}/{clash.code}/{clash.description[:40]!r} and "
                f"{event.kind}/{event.code}/{event.description[:40]!r}"
            )
        seen[event.eid] = event
    return out
