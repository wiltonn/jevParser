"""Pair rows across two model years.

Deterministic tiers first, cheapest signal first.  The tiers exist in this order
because each one is free and each removes a class of false "new feature" claims
that a later, more expensive tier would get wrong.

Two things this module deliberately does **not** do:

*   **No cross-sheet pairing.** 77-79% of option codes appear on more than one
    sheet within the same year, so matching across sheets would link
    Equipment-Groups-2026 to Interior-2027 for hundreds of codes and manufacture
    phantom "moved sheet" events.  Multi-sheet presence is reported, not paired.
*   **No greedy fuzzy matching.** That is left to the assignment solver in the
    next layer, because greedy pairing mis-matches ``PDN`` "AT4 Preferred
    Package" against ``PCX`` "Elevation Preferred Package" -- similar wording,
    different trims.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model.canonical import Row, Workbook
from ..rules.default_gm import DEFAULT_GM
from ..rules.model import PairingRules, RuleSet, TierRule

#: Tier 1 -- full identity, including both code columns and the description.
#: Tier 2 -- unordered code set plus description.
#: Tier 3 -- unordered code set alone.  This is the free win: it catches every
#:           orderable <-> reference-only column swap, which is a real and
#:           high-order-risk change that an ordered-pair key misses entirely.
#: Tier 4 -- description alone, for rows that carry no code at all.
#: Tier 5 -- description alone, only where it is unique on both sides.
#:
#: The tiers are ruleset data (``rules.PairingRules``): a ruleset may reorder,
#: disable or relabel them but chooses keys only from :data:`_KEYFUNCS`.  This
#: is the GM default in its original shape.
TIERS: tuple[tuple[int, str, str], ...] = tuple(
    (t.id, t.key, t.reason) for t in DEFAULT_GM.pairing.tiers
)


@dataclass(slots=True)
class Pair:
    a: Row
    b: Row
    tier: int
    reason: str

    @property
    def code_column_swapped(self) -> bool:
        """Orderable <-> reference-only.  Meaningful: an orderable code can be
        put on an order, a reference-only code cannot."""
        return (self.a.orderable_rpo or None) != (self.b.orderable_rpo or None) and \
               self.a.codes == self.b.codes

    @property
    def description_changed(self) -> bool:
        return self.a.description != self.b.description

    @property
    def section_changed(self) -> bool:
        return self.a.section_path != self.b.section_path


@dataclass(slots=True)
class SheetAlignment:
    sheet: str
    pairs: list[Pair] = field(default_factory=list)
    only_a: list[Row] = field(default_factory=list)
    only_b: list[Row] = field(default_factory=list)

    def tier_counts(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for p in self.pairs:
            out[p.tier] = out.get(p.tier, 0) + 1
        return out


def _key_exact(row: Row):
    return (row.orderable_rpo, row.ref_rpo, row.description)


def _key_codeset_desc(row: Row):
    return (row.codes, row.description) if row.codes else None


def _key_codeset(row: Row):
    return row.codes if row.codes else None


def _key_desc(row: Row):
    return row.description if not row.codes and row.description else None


def _key_unique_desc(row: Row):
    """Description alone, regardless of codes.

    Only safe because :func:`_match_tier` is given pools already stripped of
    everything the earlier tiers matched, and because the caller restricts this
    tier to descriptions that occur exactly once on each side.
    """
    return row.description or None


_KEYFUNCS = {
    "exact": _key_exact,
    "code_set_and_description": _key_codeset_desc,
    "code_set": _key_codeset,
    "description": _key_desc,
    "unique_description": _key_unique_desc,
}


def _match_tier(pool_a: list[Row], pool_b: list[Row],
                tier_rule: TierRule) -> tuple[list[Pair], list[Row], list[Row]]:
    """Bucket both pools by the tier's key and pair within each bucket.

    Duplicate rows (``SFE`` appears twice in Equipment Groups, in different
    sections) land in the same bucket, so they are paired by section first and
    then by their original order, which keeps a stable one-to-one mapping.
    """
    keyfunc = _KEYFUNCS[tier_rule.key]
    tier, reason = tier_rule.id, tier_rule.reason
    buckets_a: dict[object, list[Row]] = {}
    buckets_b: dict[object, list[Row]] = {}
    for row in pool_a:
        k = keyfunc(row)
        if k is not None:
            buckets_a.setdefault(k, []).append(row)
    for row in pool_b:
        k = keyfunc(row)
        if k is not None:
            buckets_b.setdefault(k, []).append(row)

    pairs: list[Pair] = []
    used_a: set[int] = set()
    used_b: set[int] = set()

    for key, rows_a in buckets_a.items():
        rows_b = buckets_b.get(key)
        if not rows_b:
            continue
        if tier_rule.unique_only and (len(rows_a) != 1 or len(rows_b) != 1):
            continue
        # Prefer a partner in the same section before falling back to order.
        remaining_b = list(rows_b)
        for row_a in rows_a:
            match = next(
                (r for r in remaining_b if r.section_path == row_a.section_path),
                None,
            ) or (remaining_b[0] if remaining_b else None)
            if match is None:
                break
            remaining_b.remove(match)
            pairs.append(Pair(a=row_a, b=match, tier=tier, reason=reason))
            used_a.add(id(row_a))
            used_b.add(id(match))

    left_a = [r for r in pool_a if id(r) not in used_a]
    left_b = [r for r in pool_b if id(r) not in used_b]
    return pairs, left_a, left_b


def align_sheet(rows_a: list[Row], rows_b: list[Row], sheet: str,
                pairing: PairingRules | None = None) -> SheetAlignment:
    """Run the deterministic tiers in order over one sheet."""
    result = SheetAlignment(sheet=sheet)
    pool_a, pool_b = list(rows_a), list(rows_b)
    for tier_rule in (pairing or DEFAULT_GM.pairing).tiers:
        if not tier_rule.enabled:
            continue
        pairs, pool_a, pool_b = _match_tier(pool_a, pool_b, tier_rule)
        result.pairs.extend(pairs)
    result.only_a, result.only_b = pool_a, pool_b
    return result


@dataclass(slots=True)
class Arbitration:
    """What the ``All`` sheet says about a code that the matrix could not pair.

    ``All`` is a per-year ``code -> description`` dictionary, so it settles
    add-versus-relocate without any inference: a code present in both years
    cannot be a new feature, whatever a matrix row suggests.
    """

    retired: frozenset[str]        # in 2026's dictionary, gone from 2027's
    introduced: frozenset[str]     # new in 2027's dictionary
    reworded: dict[str, tuple[str, str]]
    present_both: frozenset[str]

    def verdict(self, code: str | None) -> str:
        if not code:
            return "unknown"
        if code in self.present_both:
            return "relocated_or_reworded"
        if code in self.retired:
            return "retired"
        if code in self.introduced:
            return "introduced"
        return "absent_from_dictionary"


def arbitrate(book_a: Workbook, book_b: Workbook,
              dictionary_sheet: str | None = "All") -> Arbitration:
    def dictionary(book: Workbook) -> dict[str, str]:
        sheet = book.sheets.get(dictionary_sheet) if dictionary_sheet else None
        if sheet is None:
            return {}
        return {r.orderable_rpo: r.description for r in sheet.rows if r.orderable_rpo}

    da, db = dictionary(book_a), dictionary(book_b)
    both = frozenset(da) & frozenset(db)
    return Arbitration(
        retired=frozenset(da) - frozenset(db),
        introduced=frozenset(db) - frozenset(da),
        reworded={c: (da[c], db[c]) for c in both if da[c] != db[c]},
        present_both=both,
    )


#: How an unpaired row should be reported.  Derived from the ``All`` dictionary,
#: so all but the last are deterministic and need no judgment call.
DISPOSITIONS = (
    "retired",            # code left the dictionary: genuinely gone
    "introduced",         # code entered the dictionary: genuinely new
    "moved_off_sheet",    # still in the dictionary, no longer listed on this sheet
    "moved_onto_sheet",   # still in the dictionary, newly listed on this sheet
    "listing_removed",    # still listed on this sheet, but one fewer time
    "listing_added",      # still listed on this sheet, but one more time
    "needs_review",       # no code to arbitrate with: a fuzzy/judgment candidate
)


@dataclass(slots=True)
class Unpaired:
    """An unpaired row plus the disposition the dictionary implies for it.

    A code that is present in *both* years' dictionaries but unpaired on a given
    sheet has not been retired -- it has moved between sheets, which is ordinary
    and happens because most codes are listed on several sheets at once.  Calling
    that a contradiction would either fail the run spuriously or report a
    retirement that did not happen.
    """

    row: Row
    year: str
    sheet: str
    verdict: str
    disposition: str


def _disposition(row: Row, year: str, arb: "Arbitration",
                 paired_codes: frozenset[str]) -> str:
    """Classify an unpaired row using only the dictionary and the sheet itself.

    ``paired_codes`` guards against a wrong statement: a code listed twice in one
    year and once in the other leaves a residue row even though the code is still
    present on the sheet.  That is a listing count change, not a relocation --
    reporting it as "moved off Equipment Groups" would be false.
    """
    verdict = arb.verdict(row.code)
    if verdict == "relocated_or_reworded":
        if row.code in paired_codes:
            return "listing_removed" if year == "a" else "listing_added"
        return "moved_off_sheet" if year == "a" else "moved_onto_sheet"
    if verdict in ("retired", "introduced"):
        return verdict
    return "needs_review"


@dataclass(slots=True)
class BookAlignment:
    sheets: dict[str, SheetAlignment]
    arbitration: Arbitration
    residue: list[Unpaired] = field(default_factory=list)

    @property
    def pairs(self) -> list[Pair]:
        return [p for s in self.sheets.values() for p in s.pairs]

    def unpaired(self) -> tuple[list[Row], list[Row]]:
        a = [r for s in self.sheets.values() for r in s.only_a]
        b = [r for s in self.sheets.values() for r in s.only_b]
        return a, b

    def by_disposition(self) -> dict[str, list[Unpaired]]:
        out: dict[str, list[Unpaired]] = {d: [] for d in DISPOSITIONS}
        for item in self.residue:
            out[item.disposition].append(item)
        return out

    @property
    def review_candidates(self) -> list[Unpaired]:
        """The only rows that need more than deterministic reasoning."""
        return [r for r in self.residue if r.disposition == "needs_review"]


def align_books(book_a: Workbook, book_b: Workbook,
                rules: RuleSet | None = None) -> BookAlignment:
    """Align every comparable sheet, then let the dictionary arbitrate the residue."""
    rules = rules or DEFAULT_GM
    dictionary_sheet = rules.sheets.dictionary_sheet
    arb = arbitrate(book_a, book_b, dictionary_sheet)
    sheets: dict[str, SheetAlignment] = {}
    for name, sheet_a in book_a.sheets.items():
        if name == dictionary_sheet:
            continue            # the dictionary is the arbiter, not a diff target
        sheet_b = book_b.sheets.get(name)
        if sheet_b is None:
            continue
        sheets[name] = align_sheet(sheet_a.rows, sheet_b.rows, name, rules.pairing)

    # Let the dictionary dispose of the residue.  Everything except
    # "needs_review" is settled here, with no inference.
    residue: list[Unpaired] = []
    for name, alignment in sheets.items():
        paired_codes = frozenset(
            c for p in alignment.pairs for c in (p.a.codes | p.b.codes)
        )
        for year, pool in (("a", alignment.only_a), ("b", alignment.only_b)):
            for row in pool:
                residue.append(Unpaired(
                    row=row, year=year, sheet=name,
                    verdict=arb.verdict(row.code),
                    disposition=_disposition(row, year, arb, paired_codes),
                ))
    return BookAlignment(sheets=sheets, arbitration=arb, residue=residue)
