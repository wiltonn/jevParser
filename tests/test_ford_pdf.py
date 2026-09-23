"""The Ford PDF reader, against the 2025 and 2026 Mustang order guides.

Every expectation about *changes* below is taken from Ford's own "Major
Product Changes" pages in the 2026 guide, which the comparison deliberately
does not read -- so they are an independent check on it.
"""

from collections import Counter
from pathlib import Path

import pytest

pytest.importorskip("pdfplumber")

from jev_diff.rules.default_ford import DEFAULT_FORD_PDF as FORD  # noqa: E402

MUSTANG = Path(__file__).resolve().parents[1] / "docs" / "content" / "Ford" / "Mustang"
PDFS = {y: MUSTANG / f"{y}-Mustang-Order-Guide.pdf" for y in ("2025", "2026")}


@pytest.fixture(scope="module")
def ford_books():
    from jev_diff.pipeline import parse_document

    return {y: parse_document(str(p), y, FORD) for y, p in PDFS.items()}


@pytest.fixture(scope="module")
def ford_result(ford_books):
    from jev_diff.pipeline import compare

    return compare(str(PDFS["2025"]), str(PDFS["2026"]), label_a="2025", label_b="2026",
                   rules=FORD, books=(ford_books["2025"], ford_books["2026"]))


def _events(result, kind=None):
    return [e for e in result.events if kind is None or e.kind == kind]


# -- reading ------------------------------------------------------------------------

def test_every_page_is_recognised(ford_books):
    for book in ford_books.values():
        assert not [w for w in book.warnings if w.kind == "unknown_section"]
        assert not [w for w in book.warnings if w.severity == "fatal"]


def test_sections_become_sheets(ford_books):
    b26 = ford_books["2026"].sheets
    assert {"Equipment Groups", "Color & Trim", "Stripe Offerings", "Seat Belt Colors",
            "Rear Axle Ratios", "All codes"} <= set(b26)
    assert {f"{s} – Standard Equipment" for s in
            ("EcoBoost", "EcoBoost Premium", "GT", "GT Premium", "Dark Horse")} <= set(b26)
    # 2025 had no seat-belt colour page.
    assert "Seat Belt Colors" not in ford_books["2025"].sheets


def test_equipment_groups_are_columns(ford_books):
    sheet = ford_books["2026"].sheets["Equipment Groups"]
    assert [c.axis_id for c in sheet.columns] == [
        "100A", "101A", "200A", "201A", "300A", "301A", "400A", "401A", "600A", "700A"]
    by_code = {r.code: r for r in sheet.rows if r.code}
    # Standard on every EcoBoost, not listed (so not offered) on the V8 series.
    assert by_code["P8T"].values["100A"].token == "S"
    assert by_code["P8T"].values["300A"].token == ""
    # Compound cells keep both halves.
    assert by_code["13A"].values["101A"].token == "S/O"
    # Notes become typed conditions citing codes.
    relations = {(c.relation, tuple(sorted(c.referenced_codes)))
                 for c in by_code["60L"].values["100A"].conditions}
    assert ("not_available_with", ("60G", "642")) in relations
    # Package contents become clauses, so content changes diff clause by clause.
    assert any(c.relation == "package_content" for c in by_code["60L"].values["100A"].conditions)


def test_grid_names_survive_split_rows(ford_books):
    paints = {r.code: r.display_description
              for r in ford_books["2025"].sheets["Color & Trim"].rows}
    assert paints["B7"] == "Intense Lime Yellow Metallic"
    assert paints["K1"] == "Vapor Blue Metallic"
    assert paints["DT"] == "Molten Magenta Metallic Tri-coat"


def test_grid_columns_are_trim_codes(ford_books):
    sheet = ford_books["2026"].sheets["Seat Belt Colors"]
    ids = [c.axis_id for c in sheet.columns]
    assert "100A:DW" in ids and "401A:53" in ids and "700A:AA/WA" in ids
    # The GT block prints no trim-code row; it inherits the codes above it.
    assert "300A:DW" in ids
    prime_blue = next(r for r in sheet.rows if r.code == "59L")
    assert prime_blue.values["200A:3J"].token == "--"
    assert prime_blue.values["100A:DW"].token == "O"


def test_standard_equipment_lists(ford_books):
    rows = ford_books["2026"].sheets["EcoBoost – Standard Equipment"].rows
    texts = {r.display_description for r in rows}
    assert "Rear-window Defroster" in texts
    assert "Dual Zone Auto Climate Control" in texts          # a sub-item
    acn = next(r for r in rows if r.display_description.startswith("Active Noise"))
    assert acn.display_description == "Active Noise Cancellation (Convertible Only)"
    assert any(c.relation == "limited_to" for c in acn.values["standard"].conditions)


def test_dictionary_arbitrates(ford_books):
    codes = {r.code for r in ford_books["2026"].sheets["All codes"].rows}
    assert {"P8T", "60L", "B2", "59L"} <= codes


# -- comparing, against Ford's own change summary -----------------------------------

@pytest.mark.parametrize("code", ["N4", "B7", "605", "64P", "54F", "606"])
def test_deletions_ford_lists_are_retired(ford_result, code):
    """Deleted colours, the Wheel & Stripe Package, carbon wheels, and the
    California Special and 60th Anniversary packages."""
    assert any(e.code == code and e.disposition == "retired"
               for e in _events(ford_result, "row_removed"))


@pytest.mark.parametrize("code", ["B2", "NL", "59K", "59L", "59M", "54B", "52V", "13R"])
def test_additions_ford_lists_are_introduced(ford_result, code):
    assert any(e.code == code and e.disposition == "introduced"
               for e in _events(ford_result, "row_added"))


def test_items_ford_lists_as_deleted_leave_the_lists(ford_result):
    removed = {e.description for e in _events(ford_result, "row_removed")}
    for text in ("Universal Garage Door Opener (UGDO)", "Illuminated Door-sill Scuff Plates",
                 "Seat Belt – Deep Indigo"):
        assert text in removed, text


def test_floor_mats_no_longer_standard(ford_result):
    """66B left the EcoBoost standard list and became an option."""
    assert any(e.description == "Floor Mats – 1st Row Carpeted, Black" and
               e.sheet == "EcoBoost – Standard Equipment" for e in _events(ford_result, "row_removed"))
    assert any(e.code == "66B" for e in _events(ford_result, "row_added"))


def test_dark_horse_manual_becomes_optional(ford_result):
    """"10-speed Automatic is standard on Dark Horse; TREMEC 6-speed Manual is optional"."""
    event = next(e for e in _events(ford_result, "availability") if e.code == "44E")
    assert {d.direction for d in event.deltas if d.changed} == {"decontented"}


def test_seat_belt_page_is_reported_as_new(ford_result):
    assert any(e.kind == "identity" and "added: Seat Belt Colors" in e.detail
               for e in ford_result.events)


def test_conditions_diff_at_clause_level(ford_result):
    """Removing the Wheel & Stripe Package dropped its exclusion clause from
    the Over-the-Top stripe, and nothing else about the stripe changed."""
    event = next(e for e in ford_result.events if e.code == "47B")
    dropped = {c.raw for d in event.deltas for c in d.constraints.removed}
    assert dropped == {"Not available with Wheel & Stripe Package (605)"}
    assert not any(d.constraints.added for d in event.deltas)


def test_trademark_marks_are_not_changes(ford_result):
    assert not any(e.kind == "description" and "Co-Pilot360" in e.detail
                   for e in ford_result.events)


def test_event_mix_is_stable(ford_result):
    kinds = Counter(e.kind for e in ford_result.events)
    assert kinds["row_removed"] >= 50 and kinds["row_added"] >= 45
    assert kinds["constraint"] >= 30 and kinds["availability"] >= 8
    assert not ford_result.fatal
