"""Parse-layer golden fixture.

These cases are verified ground truth from the 2026/2027 GMC Yukon exports and
are the specification for the differ.  Each one is a case where a naive diff
gets the answer wrong, so they are asserted at the level that matters: resolved
constraints, never footnote numbers.
"""

from conftest import relations


def test_both_workbooks_parse_without_warnings(books):
    for label, book in books.items():
        assert book.warnings == [], f"{label}: {[w.message for w in book.warnings]}"
        assert len(book.sheets) == 14


def test_feature_matrix_row_counts(books):
    def feature_rows(book):
        return sum(len(s.rows) for s in book.sheets.values()
                   if s.kind == "feature_matrix")

    assert feature_rows(books["2026"]) == 624
    assert feature_rows(books["2027"]) == 609


def test_legend_includes_tokens_absent_from_the_data(books):
    """The legend declares D, upgradeable and multi-model, none of which appear
    in this corpus.  They must still be captured, or a future year that uses
    them lands in the unparsed bucket."""
    legend = books["2026"].sheets["Standard Equipment"].legend
    assert set(legend.tokens) >= {"S", "A", "--", "D", "■", "□", "*"}
    # The competing sheet-scoped digit namespace from row 2.
    assert legend.sheet_notes == {1: "Available on T*10706", 2: "Available on TK10706"}


def test_5jl_constraint_removed_on_three_trims_denali_unchanged(find):
    """The headline case.  Raw cells change in all five columns
    (A/D1 A/D1 A/D1 A/D2 A/D2 -> A/D A/D A/D A/D1 A/D1), but the real change is
    that the "requires 20-inch or larger wheels" constraint was dropped for the
    first three trims.  Denali must come out unchanged."""
    a, b = find("2026", "Equipment Groups", "5JL"), find("2027", "Equipment Groups", "5JL")

    for trim in ("4SA1", "4SB2", "4SG2"):
        assert any(r == "requires" for r, _ in relations(a.values[trim]))
        assert relations(b.values[trim]) == set(), f"{trim} should be unconstrained in 2027"

    for trim in ("5SA1", "5SB2"):
        assert relations(a.values[trim]) == relations(b.values[trim]), \
            f"{trim} must be unchanged"

    # Token itself never changed; only the conditions did.
    assert all(a.values[t].token == b.values[t].token for t in a.values)


def test_sfe_denali_ultimate_gained_an_exclusion(find):
    """The inverse failure: exactly one cell changes (■3 -> ■2), which reads like
    renumbering, but the resolved text differs -- Denali Ultimate gained the
    (SPZ) exclusion.  Resolving clauses makes this deterministic."""
    a, b = find("2026", "Equipment Groups", "SFE"), find("2027", "Equipment Groups", "SFE")

    gained = relations(b.values["5SB2"]) - relations(a.values["5SB2"])
    assert gained == {("not_available_with", ("SPZ",))}

    # The other package trim cites a different footnote number across the two
    # years but identical text, so it must read as unchanged.
    assert relations(a.values["4SG2"]) == relations(b.values["4SG2"])


def test_ulm_denali_moved_from_optional_to_package_included(find):
    a, b = find("2026", "Interior", "ULM"), find("2027", "Interior", "ULM")
    assert a.values["5SA1"].token.value == "A"
    assert relations(a.values["5SA1"]) == {("included_only_with", ("UKL",))}
    assert b.values["5SA1"].token.value == "■"
    assert relations(b.values["5SA1"]) == set()


def test_new_prefix_alone_is_not_a_change(books):
    """RFO and SFU only lost GM's own NEW! marker between years."""
    def desc(label, code):
        for row in books[label].sheets["All"].rows:
            if row.orderable_rpo == code:
                return row
        raise AssertionError(f"{code} missing from {label}")

    for code in ("RFO", "SFU"):
        a, b = desc("2026", code), desc("2027", code)
        assert a.display_description != b.display_description   # raw text differs
        assert a.description == b.description                   # normalized does not


def test_description_normalization_preserves_trailing_digits():
    """Footnote digits ride on the availability token, not the description, so a
    trailing digit in a description is data: stripping it would corrupt engine
    names."""
    from jev_diff.parse.footnotes import normalize_description, normalize_label

    assert normalize_description("Engine, 5.3L EcoTec3 V8") == "engine, 5.3l ecotec3 v8"
    # Labels on footer-footnote sheets are the opposite case.
    assert normalize_label("Titanium Rush Metallic4") == "titanium rush metallic"


def test_colour_and_trim_keys_on_paint_code_not_label(books):
    """The footnote definitions were permuted between years, so every paint
    label's digit shifted while the paints did not.  Keying on the colour code
    must show exactly one paint added and one removed."""
    def paints(label):
        return {
            row.extra["code"]
            for row in books[label].sheets["Color and Trim"].rows
            if row.extra.get("code", "").startswith("G")
        }

    a, b = paints("2026"), paints("2027")
    assert b - a == {"G42"}, "Coastal Dune added"
    assert a - b == {"GBD"}, "Midnight Pine removed"


def test_specs_lost_the_18_inch_turning_diameter_row(books):
    a = books["2026"].sheets["Specs"].rows
    b = books["2027"].sheets["Specs"].rows
    removed = {r.description for r in a} - {r.description for r in b}
    assert any("18" in d for d in removed), removed
    assert len(books["2026"].sheets["Dimensions"].rows) == \
           len(books["2027"].sheets["Dimensions"].rows)


def test_most_codes_appear_on_several_sheets(books):
    """The finding that rules out a cross-sheet pairing tier: if most codes are
    multi-sheet, cross-sheet matching manufactures phantom "moved" events."""
    for label in ("2026", "2027"):
        index = books[label].code_index()
        multi = [c for c, sheets in index.items() if len(sheets) > 1]
        assert len(multi) / len(index) > 0.7
        assert books[label].sheets["Interior"] and "UKL" in index
        assert {"Equipment Groups", "Interior"} <= index["UKL"]


def test_wheels_sheet_owns_the_images_and_2027_has_a_placeholder(books):
    """openpyxl loads none of these, which is why the annotator patches the zip.
    The placeholder swap is itself a content-ops finding."""
    a = books["2026"].sheets["Wheels"].images
    b = books["2027"].sheets["Wheels"].images
    assert len(a) == 18 and len(b) == 18
    assert not [i for i in a if i.size < 10_000], "2026 has no placeholders"

    # 2027 anchors one 4KB PNG placeholder at TWO separate wheel rows, where
    # 2026 had real photographs -- so two wheels ship with no image at all.
    placeholders = [i for i in b if i.size < 10_000]
    assert len(placeholders) == 2
    assert len({i.md5 for i in placeholders}) == 1
    assert {i.mime for i in placeholders} == {"png"}
    assert sorted(i.anchor_row for i in placeholders) == [6, 13]
