"""Pairing-layer golden fixture.

This is the measurement baseline for every later phase: if a change to the
parser, the tiers or the arbiter moves any of these numbers, it should be a
deliberate decision, not a surprise.
"""

from collections import Counter


def test_tier_yields(alignment):
    """Deterministic tiers do nearly all the work.

    Tier 3 (unordered code set) is the one worth protecting: it exists purely to
    catch orderable <-> reference-only swaps, which an ordered (orderable, ref)
    key misses entirely.
    """
    tiers = Counter()
    for sheet in alignment.sheets.values():
        tiers.update(sheet.tier_counts())
    assert tiers[1] == 629
    assert tiers[2] == 4
    assert tiers[3] == 28
    assert sum(tiers.values()) == 661


def test_residue_is_mostly_settled_without_judgment(alignment):
    counts = {k: len(v) for k, v in alignment.by_disposition().items() if v}
    assert counts == {
        "retired": 22,
        "introduced": 8,
        "moved_off_sheet": 3,
        "moved_onto_sheet": 4,
        "listing_removed": 1,
        "listing_added": 1,
        "needs_review": 9,
    }
    # Only 9 of 48 residue rows need more than the dictionary to classify.
    assert len(alignment.review_candidates) == 9


def test_dictionary_arbitration(alignment):
    arb = alignment.arbitration
    assert arb.retired == frozenset(
        {"02N", "PCL", "PCR", "PDN", "R7S", "RG7", "RGH", "RSL", "SMR", "UV3", "VAV"}
    )
    assert arb.introduced == frozenset({"PCX", "R4K", "R4S"})
    assert set(arb.reworded) == {"PRB", "R9L"}


def test_orderable_to_reference_only_swaps_are_caught_deterministically(alignment):
    """The highest-order-risk deterministic finding: an orderable code that
    became reference-only can no longer be put on an order."""
    swaps = {
        (sheet.sheet, next(iter(pair.a.codes)))
        for sheet in alignment.sheets.values()
        for pair in sheet.pairs
        if pair.code_column_swapped
    }
    assert {c for _, c in swaps} == {"D07", "DCH", "UKL", "UW9"}
    assert ("Equipment Groups", "UKL") in swaps
    assert ("Interior", "D07") in swaps
    assert len(swaps) == 8, "each swap is reported per sheet it appears on"


def test_pdn_is_never_paired_with_pcx(alignment):
    """The nastiest trap in this corpus: "AT4 Preferred Package" and "Elevation
    Preferred Package" read alike but are different trims.  They must stay a
    retirement plus an introduction."""
    for sheet in alignment.sheets.values():
        for pair in sheet.pairs:
            assert {pair.a.code, pair.b.code} != {"PDN", "PCX"}

    dispositions = {
        (u.row.code, u.disposition) for u in alignment.residue
    }
    assert ("PDN", "retired") in dispositions
    assert ("PCX", "introduced") in dispositions


def test_wheel_codes_are_retired_and_introduced_not_paired(alignment):
    """RSL->R4S and SMR->R4K are the same product concept under a new order
    code.  Pairing them would synthesize a meaningless availability transition
    across two different codes, so they stay separate and get a succession link
    in the next layer instead."""
    for sheet in alignment.sheets.values():
        for pair in sheet.pairs:
            assert pair.a.code not in ("RSL", "SMR")

    by_code = {}
    for u in alignment.residue:
        by_code.setdefault(u.row.code, set()).add(u.disposition)
    assert by_code["RSL"] == {"retired"}
    assert by_code["SMR"] == {"retired"}
    assert by_code["R4S"] == {"introduced"}
    assert by_code["R4K"] == {"introduced"}


def test_c3q_moved_between_sheets(alignment):
    """C3Q left Equipment Groups and appeared on Standard Equipment.  The
    dictionary proves it is neither new nor retired, so it must be reported as a
    relocation -- which is why there is no cross-sheet pairing tier."""
    moves = {
        (u.sheet, u.disposition)
        for u in alignment.residue
        if u.row.code == "C3Q"
    }
    assert ("Equipment Groups", "moved_off_sheet") in moves
    assert ("Standard Equipment", "moved_onto_sheet") in moves


def test_ukl_duplicate_listing_is_not_reported_as_a_move(alignment):
    """UKL is listed twice in 2026's Equipment Groups and once in 2027's.  The
    leftover row must not claim UKL left the sheet -- it is still there."""
    ukl = [u for u in alignment.residue if u.row.code == "UKL"]
    assert [u.disposition for u in ukl] == ["listing_removed"]


def test_paints_pair_on_colour_code_not_label(alignment):
    """Every paint label's footnote digit shifted because the footnote
    definitions were permuted, so label-keyed pairing would report the whole
    paint table as churn."""
    paints = alignment.sheets["Color and Trim"].pairs
    paired = {(p.a.orderable_rpo, p.b.orderable_rpo)
              for p in paints if p.a.orderable_rpo}
    assert paired == {(c, c) for c in
                      ("G4J", "G6M", "GAZ", "GBA", "GNT", "GXD", "GXP")}

    review = {u.row.extra.get("code") for u in alignment.review_candidates
              if u.sheet == "Color and Trim"}
    assert review == {"G42", "GBD"}


def test_no_cross_sheet_pairs_exist(alignment):
    for name, sheet in alignment.sheets.items():
        for pair in sheet.pairs:
            assert pair.a.sheet == pair.b.sheet == name
