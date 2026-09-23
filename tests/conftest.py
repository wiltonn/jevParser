import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

GMC = ROOT / "docs" / "content" / "GMC"
BOOKS = {
    "2026": GMC / "2026 GMC Yukon _ Denali Export.xlsx",
    "2027": GMC / "2027 GMC Yukon Export.xlsx",
}


@pytest.fixture(scope="session")
def books():
    from jev_diff.parse.registry import parse_workbook

    return {label: parse_workbook(str(path), label) for label, path in BOOKS.items()}


@pytest.fixture(scope="session")
def find(books):
    def _find(label, sheet, code):
        for row in books[label].sheets[sheet].rows:
            if code in row.codes:
                return row
        return None

    return _find


def relations(cell):
    """The set of (relation, cited codes) on a cell -- footnote numbers absent."""
    return {(c.relation, tuple(sorted(c.referenced_codes))) for c in cell.conditions}


@pytest.fixture(scope="session")
def alignment(books):
    from jev_diff.align.pairing import align_books

    return align_books(books["2026"], books["2027"])


@pytest.fixture(scope="session")
def events(books, alignment):
    from jev_diff.classify import build_events, consolidate

    return consolidate(build_events(books["2026"], books["2027"], alignment))


@pytest.fixture(scope="session")
def by_code(events):
    out = {}
    for event in events:
        out.setdefault(event.code, []).append(event)
    return out
