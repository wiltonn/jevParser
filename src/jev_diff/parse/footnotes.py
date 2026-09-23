"""Footnote resolution, and the normalisation that makes comparison honest.

Two mechanisms coexist in these workbooks:

``row_list``
    On feature-matrix sheets the footnotes are a numbered list appended to the
    row's own Description cell, and the digits ride on the availability token
    (``S1``, ``A/D2``, ``■3``).

``sheet_footer``
    On Color and Trim, Specs, Dimensions, Trailering Specs and Engine Axles the
    footnotes live in one merged full-width row at the bottom of the sheet, and
    the digits ride on *row labels and column headers*
    (``Titanium Rush Metallic4``).

A third namespace competes with both: row 2 of the Description column reads
``1: Available on T*10706.  2: Available on TK10706.`` -- a sheet-scoped meaning
for the digits 1 and 2.  So resolution is ordered and records where it landed;
``unresolved`` is a fatal parse warning rather than a silently empty condition
list, because an empty list reports a false no-op.
"""

from __future__ import annotations

import re

from ..model.canonical import Constraint, FootnoteRef, Legend
from ..rules.model import ClauseRules
from .constraints import parse_footnote

# A footnote list item: "1. text" or "1: text", at the start of a line.
_ITEM_SPLIT = re.compile(r"\n(?=\d+[.:]\s)")
_ITEM_HEAD = re.compile(r"^(\d+)[.:]\s*")

# Trailing footnote digits on a label or header: "Titanium Rush Metallic4".
# Requires a preceding non-digit so "TC10706" and "5.3L" are untouched.
_LABEL_REFS = re.compile(r"(?<=[A-Za-z\)\]])(\d{1,2})\s*$")

# Legend entries are separated by runs of two or more spaces; a key may itself
# contain spaces ("-- (dashes) = Not Available"), so split on the separator
# first and only then on "=".
_LEGEND_SEP = re.compile(r"\s{2,}")
# Sheet notes read "1:  Available on T*10706.  2:  Available on TK10706."  The
# key must be a short run followed by a colon, so the "10706" inside the model
# code is not mistaken for a note number.
_SHEET_NOTE_SPLIT = re.compile(r"(?:^|\s)(\d{1,2}):\s+")

_NEW_PREFIX = re.compile(r"^\s*NEW!\s*", re.IGNORECASE)
_LOCALE = re.compile(r"\|?\s*en-us\s*$", re.IGNORECASE)


def split_description(cell: str) -> tuple[str, dict[int, str]]:
    """Description cell -> (base description, {footnote number: body}).

    The base description is everything before the first numbered item.
    """
    text = "" if cell is None else str(cell)
    parts = _ITEM_SPLIT.split(text)
    base = parts[0].strip()
    notes: dict[int, str] = {}
    for part in parts[1:]:
        m = _ITEM_HEAD.match(part.strip())
        if not m:
            continue
        notes[int(m.group(1))] = _ITEM_HEAD.sub("", part.strip()).strip()
    return base, notes


def parse_footer(cell: str) -> dict[int, str]:
    """A merged full-width footnote row -> {number: body}.

    Unlike a Description cell, the items here are newline-separated with no
    preceding base text, so every part is an item.
    """
    text = "" if cell is None else str(cell)
    notes: dict[int, str] = {}
    for part in _ITEM_SPLIT.split("\n" + text):
        stripped = part.strip()
        m = _ITEM_HEAD.match(stripped)
        if m:
            notes[int(m.group(1))] = _ITEM_HEAD.sub("", stripped).strip()
    return notes


def looks_like_footer(cell: str) -> bool:
    """Distinguish a footnote footer from a section header.

    Both are merged full-width rows.  A footer begins with a numbered item; a
    section header is a bare title like ``Additional Options``.
    """
    return bool(_ITEM_HEAD.match(str(cell or "").strip()))


def parse_legend(row1: str, row2: str) -> Legend:
    """Capture the token vocabulary and the sheet-scoped digit namespace.

    Captured per sheet-year and diffed: a legend change silently reinterprets
    thousands of cells.
    """
    raw1 = str(row1 or "").strip()
    tokens: dict[str, str] = {}
    for chunk in _LEGEND_SEP.split(raw1):
        if "=" not in chunk:
            continue
        key, _, meaning = chunk.partition("=")
        key, meaning = key.strip(), meaning.strip().rstrip(".")
        if key:
            # "-- (dashes)" is the legend's own gloss for the "--" token.
            tokens[key.split(" ")[0]] = meaning
    parts = _SHEET_NOTE_SPLIT.split(str(row2 or "").strip())
    notes: dict[int, str] = {}
    for i in range(1, len(parts) - 1, 2):
        notes[int(parts[i])] = re.sub(r"\s+", " ", parts[i + 1]).strip().rstrip(".")
    return Legend(tokens=tokens, sheet_notes=notes,
                  raw=re.sub(r"\s+", " ", raw1))


def strip_label_refs(label: str) -> tuple[str, tuple[int, ...]]:
    """``"Titanium Rush Metallic4"`` -> ``("Titanium Rush Metallic", (4,))``."""
    text = re.sub(r"\s+", " ", str(label or "")).strip()
    text = _LOCALE.sub("", text).strip()
    nums: tuple[int, ...] = ()
    m = _LABEL_REFS.search(text)
    if m:
        nums = (int(m.group(1)),)
        text = text[: m.start()].strip()
    return text, nums


def resolve(
    numbers: tuple[int, ...],
    *,
    row_notes: dict[int, str] | None = None,
    sheet_notes: dict[int, str] | None = None,
    legend_notes: dict[int, str] | None = None,
) -> tuple[FootnoteRef, ...]:
    """Dereference footnote numbers, recording which namespace answered.

    Priority: the row's own list, then the sheet-scoped footer, then the row-2
    legend namespace.  Anything left is ``unresolved`` and must fail the run.
    """
    out: list[FootnoteRef] = []
    for n in numbers:
        for table, origin in (
            (row_notes, "row_list"),
            (sheet_notes, "row_list"),
            (legend_notes, "sheet_legend"),
        ):
            if table and n in table:
                out.append(FootnoteRef(n, table[n], origin))  # type: ignore[arg-type]
                break
        else:
            out.append(FootnoteRef(n, None, "unresolved"))
    return tuple(out)


def constraints_for(refs: tuple[FootnoteRef, ...],
                    clauses: ClauseRules | None = None) -> tuple[Constraint, ...]:
    """Flatten resolved footnotes into deduplicated clause-level constraints."""
    seen: dict[tuple, Constraint] = {}
    for ref in refs:
        if not ref.resolved_text:
            continue
        for c in parse_footnote(ref.resolved_text, clauses):
            seen.setdefault(c.key(), c)
    return tuple(seen.values())


def normalize_description(text: str) -> str:
    """Normalise a description for *comparison* only; callers keep the raw text.

    Strips GM's own ``NEW!`` change marker -- which appears and disappears
    between years with no product meaning -- the ``|en-us`` locale suffix, and
    whitespace differences.

    Deliberately does **not** strip trailing digits.  On feature-matrix sheets
    footnote digits ride on the availability token, never on the description, so
    a trailing digit here is data: stripping it would turn
    ``"(L84) 5.3L EcoTec3 V8"`` into ``"... V"``.  Labels on footer-footnote
    sheets need that stripping and use :func:`normalize_label` instead.
    """
    s = str(text or "")
    s = s.split("\n")[0]
    s = _NEW_PREFIX.sub("", s)
    s = _LOCALE.sub("", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def normalize_label(text: str) -> str:
    """Normalise an axis label on a sheet whose footnotes live in a footer.

    Unlike a description, a trailing digit here *is* a footnote reference
    (``"Titanium Rush Metallic4"``), so it is removed.
    """
    clean, _ = strip_label_refs(str(text or ""))
    clean = _NEW_PREFIX.sub("", clean)
    return re.sub(r"\s+", " ", clean).strip().lower()
