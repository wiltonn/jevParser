"""Canonical model for GM order-guide workbooks.

Every sheet kind parses into these types, so downstream layers (align, classify,
render) never branch on sheet kind.  Two invariants carry the whole design:

1.  Footnote *numbers* never escape the parser.  A ``Cell`` holds resolved
    ``Constraint`` objects, because numbers are unstable across model years while
    meanings are what changed.
2.  Cell values are keyed by *axis identity* (a trim's order code), never by
    column index, so an inserted or reordered trim produces no spurious deltas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class Token(str, Enum):
    """Closed availability vocabulary.

    Includes the three tokens the sheet legend declares but that never appear in
    the 2026/2027 corpus (``D``, ``UPGRADEABLE``, ``MULTI_MODEL``).  They are here
    so a future model year that uses them parses rather than landing in the
    unparsed bucket and failing the run.
    """

    STANDARD = "S"
    AVAILABLE = "A"
    NOT_AVAILABLE = "--"
    ADI = "D"                 # dealer/ADI available; legend-declared, unused so far
    AVAILABLE_ADI = "A/D"     # composition of A and D; not itself in the legend
    INCLUDED = "■"       # included in equipment group
    UPGRADEABLE = "□"    # included in equipment group but upgradeable
    MULTI_MODEL = "*"
    BLANK = ""                # genuinely empty; distinct from NOT_AVAILABLE
    CODE = "<code>"           # Color and Trim cells hold RPO codes, not tokens

    @property
    def offered(self) -> bool:
        """Whether the feature can be had at all on this trim."""
        return self not in (Token.NOT_AVAILABLE, Token.BLANK)


class OpenToken(str):
    """A token from a ruleset-declared vocabulary (a PDF guide's ``O``, ``I``,
    ``O/--``), where :class:`Token` is the Excel exports' closed one.

    Quacks like a ``Token`` -- ``.value`` and ``.offered`` -- so everything
    downstream reads either without branching.
    """

    __slots__ = ()

    @property
    def value(self) -> str:
        return str(self)

    @property
    def offered(self) -> bool:
        return str(self) not in ("", "--")


# Longest-first so "A/D" wins over "A" and "--" is not read as two tokens.
_TOKEN_LITERALS = sorted(
    (t.value for t in Token if t.value not in ("", "<code>")),
    key=len,
    reverse=True,
)
_CELL_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(t) for t in _TOKEN_LITERALS) + r")?\s*(\d*)\s*$"
)

Resolution = Literal["row_list", "sheet_legend", "unresolved"]

#: A clause relation name.  The vocabulary is ruleset data
#: (``rules.ClauseRules``), plus ``"unstructured"`` for a clause no pattern
#: matched, so it is an open string rather than a closed literal.
Relation = str


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a value came from.  Needed by the zip patcher (``a1_ref``,
    ``style_index``) and by the round-trip assertion (``raw``)."""

    workbook: str
    sheet: str
    a1_ref: str
    row: int
    col: int
    raw: str
    style_index: int | None = None


@dataclass(frozen=True, slots=True)
class FootnoteRef:
    """A digit found on a cell token, row label or column header.

    ``resolved_from`` is load-bearing: the sheets carry *two* competing digit
    namespaces (a row's own footnote list, and the sheet-scoped legend in row 2
    reading ``1: Available on T*10706.``).  ``unresolved`` must fail the run
    rather than silently yield no conditions, which would report a false no-op.
    """

    number: int
    resolved_text: str | None
    resolved_from: Resolution

    @property
    def ok(self) -> bool:
        return self.resolved_from != "unresolved"


@dataclass(frozen=True, slots=True)
class Constraint:
    """A resolved condition, parsed into a relation over RPO codes.

    This is the highest-leverage type in the model: set-difference over
    ``(relation, referenced_codes)`` classifies the large majority of condition
    deltas with no model call, and yields a *directional* answer.  Only
    ``residual_text`` is ever shown to Jev.
    """

    relation: Relation
    referenced_codes: frozenset[str]
    residual_text: str
    raw: str

    def key(self) -> tuple[Relation, tuple[str, ...], str]:
        """Identity of a clause, ignoring footnote numbering and whitespace.

        Every component is sortable so callers can order clauses deterministically;
        an unordered key would make the judgment cache miss between processes.
        """
        return (self.relation, tuple(sorted(self.referenced_codes)),
                _norm(self.residual_text))


@dataclass(frozen=True, slots=True)
class Cell:
    token: Token | OpenToken
    conditions: tuple[Constraint, ...]
    prov: Provenance
    value: str | None = None       # Color and Trim: the RPO code in the cell
    refs: tuple[FootnoteRef, ...] = ()

    def key(self) -> tuple[str, str | None, tuple]:
        """Identity for comparison: token + value + the *set* of resolved
        constraints.  Footnote numbering is deliberately absent."""
        return (
            self.token.value,
            self.value,
            tuple(sorted(c.key() for c in self.conditions)),
        )


@dataclass(frozen=True, slots=True)
class AxisEntry:
    """One column (a trim) or one keyed row, aligned by identity not position."""

    axis_id: str                   # order code where available, else normalized name
    display_name: str
    raw_header: str
    index: int
    order_code: str | None = None
    refs: tuple[FootnoteRef, ...] = ()
    conditions: tuple[Constraint, ...] = ()


@dataclass(frozen=True, slots=True)
class Row:
    sheet: str
    section_path: tuple[str, ...]
    occurrence_index: int
    description: str               # normalized, for comparison
    display_description: str       # raw, for rendering
    values: dict[str, Cell]        # keyed by AxisEntry.axis_id
    prov: Provenance
    orderable_rpo: str | None = None
    ref_rpo: str | None = None
    footnotes: dict[int, str] = field(default_factory=dict)
    cited_codes: frozenset[str] = frozenset()   # codes in "(XXX)" in the description
    extra: dict[str, str] = field(default_factory=dict)  # seat code, colour code, ...
    #: Sheets this row's code appears on *within its own year*.  77-79% of codes
    #: appear on more than one sheet, which is why there is no cross-sheet
    #: pairing tier; this drives the intra-year consistency check instead.
    appears_on_sheets: frozenset[str] = frozenset()

    @property
    def codes(self) -> frozenset[str]:
        """Unordered code set.  Matching on this (rather than the ordered
        orderable/ref pair) is what catches orderable<->reference-only column
        swaps for free."""
        return frozenset(c for c in (self.orderable_rpo, self.ref_rpo) if c)

    @property
    def code(self) -> str | None:
        return self.orderable_rpo or self.ref_rpo

    def exact_key(self) -> tuple:
        return (self.sheet, self.orderable_rpo, self.ref_rpo, self.description)

    @property
    def rid(self) -> str:
        """A stable address for deep-linking one row of one worksheet.

        ``section_path`` is part of it because a code can be listed twice on the
        same sheet in different sections, and the two listings are different
        rows that a link must be able to tell apart.
        """
        import hashlib

        basis = "\0".join((
            self.sheet,
            self.code or "",
            self.description,
            " / ".join(self.section_path),
            str(self.occurrence_index),
        ))
        return hashlib.blake2b(basis.encode("utf-8"), digest_size=4).hexdigest()


@dataclass(frozen=True, slots=True)
class Legend:
    """Token vocabulary declared on a sheet.  Captured per sheet-year and diffed:
    a legend change silently reinterprets thousands of cells."""

    tokens: dict[str, str]
    sheet_notes: dict[int, str]    # the row-2 namespace: {1: "Available on T*10706."}
    raw: str


@dataclass(slots=True)
class Sheet:
    name: str
    kind: str
    rows: list[Row]
    columns: list[AxisEntry]
    legend: Legend | None = None
    footnotes: dict[int, str] = field(default_factory=dict)   # sheet-scoped table
    images: list[RowImage] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RowImage:
    """Anchored image.  Compared by content hash because filenames shift between
    years while content is identical."""

    md5: str
    mime: str
    size: int
    anchor_row: int | None
    anchor_col: int | None
    part: str


@dataclass(slots=True)
class Workbook:
    path: str
    label: str                     # e.g. "2026"
    sheets: dict[str, Sheet]
    warnings: list[Warning_] = field(default_factory=list)

    def code_index(self) -> dict[str, set[str]]:
        """code -> the worksheets that *list* it, within this year.

        77-79% of codes appear on more than one worksheet, which is why there is
        no cross-sheet pairing tier; this index powers the intra-year
        consistency check instead.

        The ``All`` sheet is excluded: it is a dictionary of every code, not a
        listing, so counting it would make every code look multi-sheet and the
        signal would mean nothing.
        """
        idx: dict[str, set[str]] = {}
        for sheet in self.sheets.values():
            if sheet.kind == "flat_lookup":
                continue
            for row in sheet.rows:
                for code in row.codes:
                    idx.setdefault(code, set()).add(sheet.name)
        return idx


@dataclass(frozen=True, slots=True)
class Warning_:
    """A parse problem.  Severity ``fatal`` exits non-zero: under-reporting is
    the failure mode that matters for this tool."""

    kind: str
    severity: Literal["fatal", "warn"]
    message: str
    where: str


def parse_cell_token(raw: str) -> tuple[Token | None, tuple[int, ...], str | None]:
    """Split a raw availability cell into (token, footnote numbers, code).

    Verified against the corpus: 21 distinct raw values, footnote runs are always
    exactly one digit, and ``--`` never carries one.  A multi-digit run is
    returned as a single number and left for the caller to flag, rather than
    guessed at as a digit sequence.
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        return Token.BLANK, (), None
    m = _CELL_RE.match(text)
    if not m:
        return None, (), text          # caller decides: RPO code, or a warning
    literal, digits = m.group(1), m.group(2)
    if literal is None:
        return None, (), text
    nums = (int(digits),) if digits else ()
    return Token(literal), nums, None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()
