"""PDF order guides -> the canonical model.

A PDF guide is a sequence of pages, each headed by a date, the model year and
series ("2026 MUSTANG® ECOBOOST® PROPRIETARY") and a section title ("EQUIPMENT
GROUP").  The ruleset's ``pdf.sections`` map section titles to layouts, and a
parser per layout turns the section's pages into one :class:`Sheet`:

``option_matrix``
    Every series' equipment-group pages merge into one sheet whose columns are
    all the equipment groups (100A, 101A, 200A, ...) and whose rows are options
    keyed by option code.  A cell a series page does not list is blank --
    not offered -- so an option newly offered on another series is an
    availability change rather than an incomparable cell.  "Note:" lines
    become conditions and bulleted package contents become ``Package
    includes`` clauses, so both diff at clause level exactly as GM footnotes do.
``feature_list``
    Bulleted standard-equipment lists, one sheet per series: the higher series'
    lists name only what they add, so merging them would misread "not listed"
    as "not standard".
``code_grid``
    Paint, seat-belt and stripe availability: rows keyed by code, columns
    identified by the codes in the header rows (interior trim ``DW``, stripe
    ``85D``), prefixed with the equipment group where one is given.
``label_grid``
    A small matrix keyed by row label (the rear-axle ratio table).

Everything is extracted with pdfplumber.  Where table extraction splits a
name over several rows -- paint names are vertically centred on their code --
names are recovered from word positions instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

from ..model.canonical import (
    AxisEntry,
    Cell,
    Constraint,
    OpenToken,
    Provenance,
    Row,
    Sheet,
    Token,
    Warning_,
    Workbook,
)
from ..parse.constraints import cited_codes, parse_clause, parse_footnote
from ..parse.footnotes import normalize_description, normalize_label
from ..rules.model import PdfRules, PdfSection, RuleSet
from .base import UnsupportedFormat

_BULLETS = "•●○◦▪"
_SUB_BULLETS = "—–"
_FOOTER = re.compile(r"= New for this model year|^- \d+ -|Ford Division$|^I = Included")
_FOOTNOTE_START = re.compile(r"^(\d{1,2})\s+(?=\S)")
_TRAILING_REF = re.compile(r"(?<=[A-Za-z®™)\]+”\"])(\d)$")
_LEADING_REF = re.compile(r"^(\d)\s*\n")


def _pdfplumber():
    try:
        import pdfplumber
    except ImportError as exc:                    # pragma: no cover - optional extra
        raise UnsupportedFormat("reading PDF guides needs the 'pdf' extra (pdfplumber)") from exc
    return pdfplumber


# --------------------------------------------------------------------------
# Pages.
# --------------------------------------------------------------------------

@dataclass(slots=True)
class _Page:
    number: int
    heading: list[str]
    section: PdfSection | None
    series: str
    footnotes: dict[int, str]
    raw: object                   # the pdfplumber page


_MARKS = re.compile(r"[®™©★]")


def _normal(name: str) -> str:
    """A name for comparison: trademark marks and ``★`` vary between years
    ("Co-Pilot360™" / "Co-Pilot360®") without meaning anything."""
    return normalize_description(_MARKS.sub("", name))


def _clean(text: str | None, marker: str = "") -> str:
    text = text or ""
    if marker:
        text = text.replace(marker, "")
    return text.strip()


def _series_name(raw: str, rules: PdfRules) -> str:
    key = re.sub(r"[®™]", "", raw).strip().upper()
    key = re.sub(r"\s+", " ", key)
    if not key:
        return ""
    if key in rules.series_names:
        return rules.series_names[key]
    return " ".join(w if len(w) <= 3 else w.title() for w in key.split())


def _footnotes_below(page, top: float) -> dict[int, str]:
    """Numbered notes printed under the last table, joined across wrapped lines."""
    region = page.crop((0, min(top, page.height), page.width, page.height))
    lines = (region.extract_text() or "").split("\n")
    return _parse_footnote_lines(lines)


def _parse_footnote_lines(lines: list[str]) -> dict[int, str]:
    notes: dict[int, str] = {}
    current: int | None = None
    for line in lines:
        text = line.strip()
        if not text or _FOOTER.search(text):
            current = None
            continue
        m = _FOOTNOTE_START.match(text)
        if m:
            current = int(m.group(1))
            notes[current] = text[m.end():].strip()
        elif current is not None:
            notes[current] = f"{notes[current]} {text}".strip()
    return notes


def _read_pages(pdf, rules: RuleSet) -> list[_Page]:
    pr = rules.pdf
    series_rx = re.compile(pr.series_pattern, re.IGNORECASE)
    pages: list[_Page] = []
    for i, page in enumerate(pdf.pages, start=1):
        lines = [l.strip() for l in (page.extract_text() or "").split("\n") if l.strip()]
        heading = lines[: pr.heading_lines]
        series = ""
        for line in heading:
            m = series_rx.search(line)
            if m:
                series = _series_name(m.group("series"), pr)
                break
        section = pr.section_for(heading)
        tables = page.find_tables()
        bottom = max((t.bbox[3] for t in tables), default=page.height * 0.8)
        pages.append(_Page(i, heading, section, series, _footnotes_below(page, bottom), page))
    return pages


# --------------------------------------------------------------------------
# Cells and descriptions.
# --------------------------------------------------------------------------

def _strip_ref(text: str, footnotes: dict[int, str]) -> tuple[str, tuple[int, ...]]:
    """``"Race Red1"`` -> ``("Race Red", (1,))`` when footnote 1 exists."""
    text = text.strip()
    refs: list[int] = []
    m = _LEADING_REF.match(text)
    if m and int(m.group(1)) in footnotes:
        refs.append(int(m.group(1)))
        text = text[m.end():]
    m = _TRAILING_REF.search(text)
    if m and int(m.group(1)) in footnotes:
        refs.append(int(m.group(1)))
        text = text[: m.start()]
    return text.strip(), tuple(refs)


def _join(lines: list[str]) -> str:
    """Join wrapped lines; a line ending in a hyphen continues the word."""
    out = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if out.endswith("-") and not out.endswith(" -"):
            out += line
        else:
            out = f"{out} {line}".strip()
    return out


def _split_description(text: str, marker: str) -> tuple[str, list[str], list[str], bool]:
    """An option cell -> (name, notes, package contents, marked new)."""
    new = marker in text if marker else False
    elements: list[list] = []                      # [kind, [lines]]
    for raw in text.replace(marker, "").split("\n") if marker else text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Note:"):
            elements.append(["note", [line[5:].strip()]])
        elif line[0] in _BULLETS:
            elements.append(["content", [line[1:].strip()]])
        elif not elements:
            elements.append(["name", [line]])
        else:
            elements[-1][1].append(line)
    # "FEDERAL/NON-CALIFORNIA EMISSIONS" over a paragraph, or a name followed
    # by "(… ordered with …)": the heading is the name, the rest reads as a note.
    for e in elements:
        if e[0] == "name" and len(e[1]) > 1:
            first, rest = e[1][0], e[1][1:]
            if (first == first.upper() and re.search(r"[A-Z]{3}", first)) or \
                    rest[0].startswith("("):
                e[1] = [first]
                elements.append(["note", [_join(rest).strip("() ")]])
            break
    name = next((_join(e[1]) for e in elements if e[0] == "name"), "")
    notes = [_join(e[1]) for e in elements if e[0] == "note"]
    contents = [_join(e[1]) for e in elements if e[0] == "content"]
    return name, notes, contents, new


class _Tokens:
    """Cell text -> token, per the ruleset's vocabulary."""

    def __init__(self, rules: RuleSet):
        pr = rules.pdf
        self.aliases = dict(pr.tokens)
        self.sep = pr.composite_separator
        self.code_rx = re.compile(pr.code_regex)
        self.group_rx = re.compile(pr.group_code_regex)
        self.marker = pr.new_marker

    def parse(self, raw: str | None) -> tuple[Token | OpenToken | None, str | None]:
        text = re.sub(r"\s+", "", _clean(raw, self.marker))
        if not text:
            return OpenToken(""), None
        if text in self.aliases:
            return OpenToken(self.aliases[text]), None
        if self.sep and self.sep in text:
            parts = text.split(self.sep)
            if all(p in self.aliases for p in parts):
                return OpenToken(self.sep.join(self.aliases[p] for p in parts)), None
        m = re.fullmatch(r"(.+?)(\d)", text)
        if m and m.group(1) in self.aliases:          # a token carrying a footnote digit
            return OpenToken(self.aliases[m.group(1)]), None
        if self.group_rx.fullmatch(text) or all(
            self.code_rx.fullmatch(p) for p in text.split("/") if p
        ):
            return Token.CODE, text
        return None, text


def _clauses_for(notes: list[str], contents: list[str], rules: RuleSet) -> tuple[Constraint, ...]:
    """Notes are conditions; package contents become ``Package includes`` clauses."""
    seen: dict[tuple, Constraint] = {}
    for note in notes:
        for c in parse_footnote(note, rules.clauses):
            seen.setdefault(c.key(), c)
    for item in contents:
        c = parse_clause(f"Package includes {item}", rules.clauses)
        seen.setdefault(c.key(), c)
    return tuple(seen.values())


# --------------------------------------------------------------------------
# Layout: option matrix.
# --------------------------------------------------------------------------

@dataclass(slots=True)
class _MergedRow:
    section: str
    name: str
    code: str | None
    raw: str
    page: int
    new: bool = False
    values: dict[str, Cell] = field(default_factory=dict)
    #: Every label seen for this row across pages; see :func:`_best_label`.
    labels: list[str] = field(default_factory=list)


def _best_label(labels: list[str], fallback: str) -> str:
    """The name printed most often for a code, ties to the longest.

    Grid pages repeat each paint once per series, and on some pages its name
    is split across rows badly enough to lose or borrow a word.  The pages
    that print it on one line agree with each other.
    """
    counts: dict[str, int] = {}
    for label in labels:
        if label:
            counts[label] = counts.get(label, 0) + 1
    if not counts:
        return fallback
    return max(counts, key=lambda l: (counts[l], len(l)))


def _option_matrix(sheet_name: str, pages: list[_Page], rules: RuleSet, label: str,
                   warnings: list[Warning_]) -> Sheet:
    pr = rules.pdf
    tok = _Tokens(rules)
    ignore = [re.compile(p, re.IGNORECASE) for p in pr.ignore_rows]
    axes: list[AxisEntry] = []
    axis_ids: set[str] = set()
    rows: dict[tuple, _MergedRow] = {}

    by_series: dict[str, list[_Page]] = {}
    for p in pages:
        by_series.setdefault(p.series, []).append(p)

    for series, series_pages in by_series.items():
        tables = [(p, t) for p in series_pages for t in p.raw.extract_tables()]
        # Each value column's equipment group: the first group code found
        # anywhere down that column (header row or entities row).
        columns: dict[int, str] = {}
        for _p, table in tables:
            for r in table:
                for j, cell in enumerate(r[2:], start=2):
                    if j in columns:
                        continue
                    m = tok.group_rx.search(_clean(cell))
                    if m:
                        columns[j] = m.group(1)
        if not columns:
            warnings.append(Warning_(
                kind="no_equipment_groups", severity="warn",
                message=f"{series or 'unnamed series'}: no equipment-group columns found",
                where=f"{label}!{sheet_name}!p{series_pages[0].number}"))
            continue
        for j in sorted(columns):
            group = columns[j]
            if group in axis_ids:
                continue
            axis_ids.add(group)
            display = f"{series} {group}".strip()
            axes.append(AxisEntry(axis_id=group, display_name=display, raw_header=display,
                                  index=len(axes) + 1, order_code=group))

        section = ""
        for page, table in tables:
            for r in table:
                cells = [c if c is not None else "" for c in r]
                desc = cells[0]
                code_cell = re.sub(r"\s+", "", _clean(cells[1] if len(cells) > 1 else "", pr.new_marker))
                vals = cells[2:]
                head = _clean(desc, pr.new_marker)
                if not head and not code_cell:
                    continue                      # blank, or a header fragment
                if head.lower() in ("series", "equipment group") or \
                        code_cell.lower() in ("option", "code", "optioncode"):
                    continue
                if any(rx.search(head) for rx in ignore):
                    continue
                if not code_cell and not any(_clean(v) for v in vals):
                    section = _section_title(head)
                    continue
                name, notes, contents, new = _split_description(desc, pr.new_marker)
                name, refs = _strip_ref(name, page.footnotes)
                notes += [page.footnotes[n] for n in refs]
                if not name:
                    continue
                code = code_cell or None
                key = (section.lower(), code or _normal(name))
                row = rows.get(key)
                if row is None:
                    row = rows[key] = _MergedRow(section, name, code, desc, page.number, new)
                conditions = _clauses_for(notes, contents, rules)
                for j, raw in enumerate(vals, start=2):
                    group = columns.get(j)
                    if group is None:
                        if _clean(raw):
                            warnings.append(Warning_(
                                kind="unplaced_value", severity="warn",
                                message=f"{raw!r} for {name[:40]!r} is in no equipment-group column",
                                where=f"{label}!{sheet_name}!p{page.number}"))
                        continue
                    token, value = tok.parse(raw)
                    if token is None:
                        warnings.append(Warning_(
                            kind="unparsed_token", severity=pr.unparsed_severity,
                            message=f"{raw!r} for {name[:40]!r} ({group}) is not a known token",
                            where=f"{label}!{sheet_name}!p{page.number}"))
                        token = Token.CODE
                    offered = token.value not in ("", "--")
                    row.values[group] = Cell(
                        token=token, conditions=conditions if offered else (), value=value,
                        prov=Provenance(label, sheet_name, f"p{page.number}", page.number, j,
                                        _clean(raw)))

    return Sheet(name=sheet_name, kind="feature_matrix",
                 rows=_finish_rows(sheet_name, rows.values(), axes, label, rules),
                 columns=axes)


def _section_title(text: str) -> str:
    """A section heading without its continuation mark or explanatory tail.

    "Dealer Installed Options (DIO) – Shipped separately from the vehicle ..."
    and "Dealer Installed Options (DIO) (continued)" are one section.
    """
    title = _join(text.split("\n"))
    title = re.sub(r"\(continued\)", "", title, flags=re.IGNORECASE)
    title = re.split(r"\s[–—-]\s", title)[0]
    return re.sub(r"\s+", " ", title).strip()


def _finish_rows(sheet_name: str, merged, axes: list[AxisEntry], label: str,
                 rules: RuleSet) -> list[Row]:
    """Fill every column (absent means not offered) and build the rows."""
    out: list[Row] = []
    seen: dict[tuple, int] = {}
    for m in merged:
        m.name = _best_label(m.labels, m.name)
        values = {}
        for axis in axes:
            values[axis.axis_id] = m.values.get(axis.axis_id) or Cell(
                token=OpenToken(""), conditions=(),
                prov=Provenance(label, sheet_name, f"p{m.page}", m.page, axis.index, ""))
        desc = _normal(m.name)
        key = (m.section, m.code, desc)
        occ = seen.get(key, 0)
        seen[key] = occ + 1
        out.append(Row(
            sheet=sheet_name, section_path=(m.section,) if m.section else (),
            occurrence_index=occ, description=desc, display_description=m.name,
            values=values, orderable_rpo=m.code,
            cited_codes=cited_codes(m.raw, rules.clauses),
            extra={"new": "1"} if m.new else {},
            prov=Provenance(label, sheet_name, f"p{m.page}", m.page, 1, m.raw[:500]),
        ))
    return out


# --------------------------------------------------------------------------
# Layout: bulleted feature list.
# --------------------------------------------------------------------------

def _is_category(line: str) -> bool:
    text = re.sub(r"\s*\(continued\)\s*$", "", line, flags=re.IGNORECASE)
    return (len(text) >= 3 and text == text.upper() and bool(re.search(r"[A-Z]{3}", text))
            and re.fullmatch(r"[A-Z0-9 /&,.'()\-®™+]+", text) is not None)


def _page_columns_text(page) -> list[str]:
    tables = page.extract_tables()
    if tables:
        return [c or "" for t in tables for r in t for c in r]
    half = page.width / 2
    return [page.crop((0, 0, half, page.height)).extract_text() or "",
            page.crop((half, 0, page.width, page.height)).extract_text() or ""]


def _feature_list(sheet_name: str, pages: list[_Page], rules: RuleSet, label: str,
                  warnings: list[Warning_]) -> Sheet:
    pr = rules.pdf
    lines: list[tuple[int, str]] = []
    for p in pages:
        for chunk in _page_columns_text(p.raw):
            for line in chunk.split("\n"):
                text = line.strip()
                # Page titles can leak into the first cell; bulleted items that
                # merely sit near the top of the page are content.
                if text and (text[0] in _BULLETS + _SUB_BULLETS or text not in p.heading):
                    lines.append((p.number, text))

    # Numbered notes come last in each column; collect them first so trailing
    # digits on items can be resolved against them.  Numbering restarts on
    # every page, so notes are kept per page.
    footnotes_by_page: dict[int, dict[int, str]] = {p.number: dict(p.footnotes) for p in pages}
    body: list[tuple[int, str]] = []
    current: int | None = None
    for number, line in lines:
        notes = footnotes_by_page.setdefault(number, {})
        m = _FOOTNOTE_START.match(line)
        if m and not line.startswith(tuple(_BULLETS + _SUB_BULLETS)):
            current = int(m.group(1))
            notes[current] = line[m.end():]
            continue
        if current is not None and not (line[0] in _BULLETS + _SUB_BULLETS
                                        or _is_category(line) or line.startswith("Note:")):
            notes[current] += " " + line
            continue
        current = None
        if not _FOOTER.search(line):
            body.append((number, line))

    @dataclass
    class Item:
        category: str
        parent: str | None
        text: list[str]
        notes: list[list[str]]
        page: int
        new: bool

    items: list[Item] = []
    category = ""
    parent: str | None = None
    target: list[str] | None = None
    for number, line in body:
        new = bool(pr.new_marker) and pr.new_marker in line
        line = line.replace(pr.new_marker, "").strip() if pr.new_marker else line
        if not line:
            continue
        if _is_category(line):
            text = re.sub(r"\s*\(continued\)\s*$", "", line, flags=re.IGNORECASE)
            # A digit on a category heading cites a disclaimer for the whole
            # group ("Driver-assist features are supplemental ..."), not a
            # condition on any item, so it is dropped rather than attached.
            category = _strip_ref(text, footnotes_by_page.get(number, {}))[0].title()
            parent, target = None, None
            continue
        if line[0] in _BULLETS:
            item = Item(category, None, [line[1:].strip()], [], number, new)
            items.append(item)
            parent, target = None, item.text
            continue
        if line[0] in _SUB_BULLETS and items:
            owner = items[-1] if items[-1].parent is None else None
            parent_text = _join(owner.text) if owner else parent
            item = Item(category, parent_text, [line[1:].strip()], [], number, new)
            items.append(item)
            parent, target = parent_text, item.text
            continue
        if line.startswith("Note:") and items:
            note: list[str] = [line[5:].strip()]
            items[-1].notes.append(note)
            target = note
            continue
        if target is not None:
            target.append(line)

    axis = AxisEntry(axis_id="standard", display_name="Standard", raw_header="Standard",
                     index=1)
    rows: list[Row] = []
    seen: dict[tuple, int] = {}
    for it in items:
        footnotes = footnotes_by_page.get(it.page, {})
        text, refs = _strip_ref(_join(it.text), footnotes)
        m = re.search(r"(?<=[a-z]) (\d)$", text)       # "Locate parked vehicle 3"
        if m and int(m.group(1)) in footnotes:
            refs += (int(m.group(1)),)
            text = text[: m.start()]
        if not text or text.lower() == "none":
            continue
        notes = [_join(n) for n in it.notes] + [footnotes[n] for n in refs]
        conditions = _clauses_for(notes, [], rules)
        path = (it.category, it.parent) if it.parent else (it.category,)
        desc = _normal(text)
        key = (path, desc)
        occ = seen.get(key, 0)
        seen[key] = occ + 1
        prov = Provenance(label, sheet_name, f"p{it.page}", it.page, 1, text)
        rows.append(Row(
            sheet=sheet_name, section_path=path, occurrence_index=occ, description=desc,
            display_description=text,
            values={"standard": Cell(token=OpenToken("S"), conditions=conditions, prov=prov)},
            cited_codes=cited_codes(text, rules.clauses),
            extra={"new": "1"} if it.new else {}, prov=prov))
    return Sheet(name=sheet_name, kind="feature_matrix", rows=rows, columns=[axis])


# --------------------------------------------------------------------------
# Layout: grids.
# --------------------------------------------------------------------------

_AXIS_PAREN = re.compile(r"\(([0-9A-Z]{3})\)")
#: A header cell whose first line is a trim code, possibly carrying a
#: footnote digit ("DH1", "531" is "53" + note 1) or a two-part code ("2A/WA").
_AXIS_CODE = re.compile(r"([0-9A-Z]{2}(?:/[0-9A-Z]{2})?)\d?")


def _header_axis(header: list[list[str]], j: int, group_rx) -> tuple[str | None, str | None]:
    """(group, code) for column ``j`` of a header block."""
    texts = [_clean(r[j]) for r in header if j < len(r) and _clean(r[j])]
    code = None
    for text in reversed(texts):
        m = _AXIS_PAREN.search(text)
        if m:
            code = m.group(1)
            break
        first = text.split("\n")[0].strip()
        m = _AXIS_CODE.fullmatch(first)
        if m is None and first and not group_rx.search(first):
            # "2A Cloth/Vinyl Seats": a code leading a line of prose.
            m = _AXIS_CODE.fullmatch(first.split()[0])
        if m:
            code = m.group(1)
            break
    group, best = None, -1
    for r in header:
        for k, cell in enumerate(r[: j + 1]):
            m = group_rx.search(_clean(cell))
            if m and k > best:
                group, best = m.group(1), k
    return group, code


def _row_labels(page, table, data_idx: list[int], top: float, bottom: float
                ) -> dict[int, str]:
    """Recover names split across table rows, from word positions.

    Table extraction cuts a multi-line name into several rows, and its code
    sits on whichever row is vertically central.  So: take the words of the
    name column, group them into lines, and split the lines into names where
    the gap between lines is wider than the leading within a name.  Each name
    goes to the data row whose code is nearest its centre.
    """
    try:
        codes = {i: (table.rows[i].cells[1] if len(table.rows[i].cells) > 1 else None)
                 or table.rows[i].bbox for i in data_idx}
        starts = [table.rows[i].cells[0][0] for i in data_idx if table.rows[i].cells[0]]
    except (IndexError, AttributeError, TypeError):
        return {}
    if not starts:
        return {}
    x0 = min(starts)
    x1 = min(b[0] for b in codes.values())     # the name column ends where codes begin
    if x1 - x0 < 10:
        return {}
    centres = {i: (b[1] + b[3]) / 2 for i, b in codes.items()}
    words = page.crop((x0, top, x1, bottom)).extract_words(keep_blank_chars=False)
    # Lines by baseline: a superscript footnote digit raises a word's top,
    # never its bottom.
    lines: list[list] = []
    for w in sorted(words, key=lambda w: w["bottom"]):
        if lines and abs(lines[-1][-1]["bottom"] - w["bottom"]) < 2.5:
            lines[-1].append(w)
        else:
            lines.append([w])
    if not lines:
        return {}
    bottoms = [max(w["bottom"] for w in line) for line in lines]
    gaps = [b - a for a, b in zip(bottoms, bottoms[1:])]
    leading = min(gaps) if gaps else 0
    blocks: list[list[list]] = [[lines[0]]]
    for line, gap in zip(lines[1:], gaps):
        if gap > leading * 1.2:
            blocks.append([line])
        else:
            blocks[-1].append(line)
    out: dict[int, list[str]] = {}
    for block in blocks:
        tops = [w["top"] for line in block for w in line]
        bots = [w["bottom"] for line in block for w in line]
        centre = (min(tops) + max(bots)) / 2
        i = min(data_idx, key=lambda k: abs(centres[k] - centre))
        for line in block:
            out.setdefault(i, []).append(" ".join(w["text"] for w in sorted(line, key=lambda w: w["x0"])))
    return {i: " ".join(v) for i, v in out.items()}


def _clean_label(text: str, footnotes: dict[int, str]) -> tuple[str, tuple[int, ...]]:
    """Drop footnote digits from a grid label, wherever they sit.

    ``"Vapor Blue 2 Metallic"`` and ``"Race Red2"`` both cite note 2.
    """
    refs: list[int] = []
    out: list[str] = []
    for w in text.split():
        if w.isdigit() and len(w) == 1:
            # A lone digit in a name is a footnote mark, printed or not.
            if int(w) in footnotes:
                refs.append(int(w))
            continue
        m = re.fullmatch(r"(.*[A-Za-z®™)\]])(\d)", w)
        if m:
            # Grid labels are colour and trim names, which never end in a
            # digit, so a glued digit is a footnote mark even when the note
            # itself was not found on the page.
            if int(m.group(2)) in footnotes:
                refs.append(int(m.group(2)))
            w = m.group(1)
        out.append(w)
    return " ".join(out), tuple(dict.fromkeys(refs))


def _code_grid(sheet_name: str, pages: list[_Page], rules: RuleSet, label: str,
               warnings: list[Warning_], *, keyed_by_code: bool = True) -> Sheet:
    pr = rules.pdf
    tok = _Tokens(rules)
    ignore = [re.compile(p, re.IGNORECASE) for p in pr.ignore_rows]
    axes: list[AxisEntry] = []
    axis_ids: set[str] = set()
    rows: dict[tuple, _MergedRow] = {}

    for page in pages:
        for table in page.raw.find_tables():
            extracted = table.extract()
            if not extracted:
                continue

            def is_data(r) -> bool:
                if len(r) < 3:
                    return False
                head = _clean(r[0], pr.new_marker)
                if keyed_by_code:
                    # A code with values is data whatever its label looks like.
                    code = re.sub(r"\s+", "", _clean(r[1], pr.new_marker))
                    return bool(tok.code_rx.fullmatch(code)) and any(_clean(c) for c in r[2:])
                if any(rx.search(head) for rx in ignore):
                    return False
                return bool(head) and any(tok.parse(c)[0] not in (None,) and _clean(c)
                                          for c in r[1:])

            def is_fragment(r) -> bool:
                return bool(_clean(r[0])) and not any(_clean(c) for c in r[1:])

            # Split into blocks: a header run, then its data rows.  Some pages
            # stack several blocks in one table.
            blocks: list[tuple[list[int], list[int]]] = []
            header: list[int] = []
            data: list[int] = []
            for i, r in enumerate(extracted):
                if not any(_clean(c) for c in r):
                    continue                      # spacer rows never end a block
                if is_data(r):
                    data.append(i)
                elif is_fragment(r) and data:
                    continue
                else:
                    if data:
                        blocks.append((header, data))
                        header, data = [], []
                    header.append(i)
            if data:
                blocks.append((header, data))

            first_col = 2 if keyed_by_code else 1
            previous_codes: dict[int, str] = {}
            for b, (header_idx, data_idx) in enumerate(blocks):
                header_rows = [[c or "" for c in extracted[i]] for i in header_idx]
                columns: dict[int, str] = {}
                width = max(len(extracted[i]) for i in data_idx)
                block_codes: dict[int, str] = {}
                for j in range(first_col, width):
                    if keyed_by_code:
                        group, code = _header_axis(header_rows, j, tok.group_rx)
                        # A stacked block may omit the trim-code row it shares
                        # with the block above; inherit it by position.
                        code = code or previous_codes.get(j)
                        if code is None:
                            continue
                        block_codes[j] = code
                        axis_id = f"{group}:{code}" if group else code
                        display = f"{group} {code}" if group else code
                    else:
                        parts = [_clean(r[j]).replace("\n", " ") for r in header_rows
                                 if j < len(r) and _clean(r[j])]
                        if not parts:
                            continue
                        display = re.sub(r"[®™]", "", " ".join(parts))
                        axis_id = normalize_label(display)
                    columns[j] = axis_id
                    if axis_id not in axis_ids:
                        axis_ids.add(axis_id)
                        axes.append(AxisEntry(axis_id=axis_id, display_name=display,
                                              raw_header=display, index=len(axes) + 1,
                                              order_code=axis_id))
                previous_codes = block_codes or previous_codes
                region_top = max((table.rows[i].bbox[3] for i in header_idx),
                                 default=table.bbox[1])
                later = [blocks[k][0][0] for k in range(b + 1, len(blocks)) if blocks[k][0]]
                region_bottom = table.rows[later[0]].bbox[1] if later else table.bbox[3]
                labels = _row_labels(page.raw, table, data_idx, region_top,
                                     region_bottom) if keyed_by_code else {}
                for i in data_idx:
                    r = [c or "" for c in extracted[i]]
                    raw_label = labels.get(i) or _join((r[0] or "").split("\n"))
                    name, refs = _clean_label(_clean(raw_label, pr.new_marker), page.footnotes)
                    new = bool(pr.new_marker) and pr.new_marker in (raw_label or "")
                    code = re.sub(r"\s+", "", _clean(r[1])) if keyed_by_code else ""
                    code = code if code and not code.startswith("*") else None
                    if not name and not code:
                        continue
                    key = (code or _normal(name),)
                    row = rows.get(key)
                    if row is None:
                        row = rows[key] = _MergedRow("", name, code, name, page.number, new)
                    row.labels.append(name)
                    row.new = row.new or new
                    conditions = _clauses_for([page.footnotes[n] for n in refs], [], rules)
                    for j, axis_id in columns.items():
                        raw = r[j] if j < len(r) else ""
                        token, value = tok.parse(raw)
                        if token is None:
                            warnings.append(Warning_(
                                kind="unparsed_token", severity=pr.unparsed_severity,
                                message=f"{raw!r} for {name[:40]!r} ({axis_id}) is not a known token",
                                where=f"{label}!{sheet_name}!p{page.number}"))
                            token = Token.CODE
                        offered = token.value not in ("", "--")
                        row.values[axis_id] = Cell(
                            token=token, conditions=conditions if offered else (), value=value,
                            prov=Provenance(label, sheet_name, f"p{page.number}", page.number,
                                            j, _clean(raw)))

    return Sheet(name=sheet_name, kind="stacked_matrix" if keyed_by_code else "grid",
                 rows=_finish_rows(sheet_name, rows.values(), axes, label, rules),
                 columns=axes)


# --------------------------------------------------------------------------
# The adapter.
# --------------------------------------------------------------------------

def _page_tops(path: str, lines: int) -> list[list[str]]:
    """The first lines of every page's text, as fast as possible.

    pdfium extracts text in a fraction of the time pdfminer needs for its
    layout analysis, which matters when an upload is identified on the spot.
    Its line order can differ slightly from pdfplumber's, which is fine for
    recognising section titles.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:                           # pragma: no cover - fallback
        pdfplumber = _pdfplumber()
        with pdfplumber.open(path) as pdf:
            return [[l.strip() for l in (p.crop((0, 0, p.width, p.height * 0.2))
                                          .extract_text() or "").split("\n") if l.strip()][:lines]
                    for p in pdf.pages]
    doc = pdfium.PdfDocument(path)
    try:
        out = []
        for page in doc:
            text = page.get_textpage().get_text_range()
            out.append([l.strip() for l in text.splitlines() if l.strip()][:lines])
        return out
    finally:
        doc.close()


@lru_cache(maxsize=8)
def _headings(path: str, mtime: float, rules_json: str) -> tuple[str, ...]:
    rules = RuleSet.from_json(rules_json)
    pr = rules.pdf
    series_rx = re.compile(pr.series_pattern, re.IGNORECASE)
    # pdfium may print "2026 MUSTANG® ECOBOOST®" without the trailing
    # "PROPRIETARY" the full parse sees, so accept either.
    loose_rx = re.compile(r"^\d{4}\s+\S+\s*(?P<series>.*?)\s*(?:PROPRIETARY)?$", re.IGNORECASE)
    names: list[str] = []
    for i, heading in enumerate(_page_tops(path, pr.heading_lines + 2), start=1):
        m = next((m for m in (series_rx.search(l) or loose_rx.search(l) for l in heading) if m),
                 None)
        series = _series_name(m.group("series"), pr) if m else ""
        name = _sheet_name(_Page(i, heading, pr.section_for(heading), series, {}, None))
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _sheet_name(page: _Page) -> str | None:
    if page.section is None or page.section.layout == "ignore":
        return None
    return page.section.sheet.replace("{series}", page.series or "Unnamed")


class PdfAdapter:
    format = "pdf"
    extensions = (".pdf",)

    def sniff(self, path: str) -> bool:
        try:
            with open(path, "rb") as fh:
                return fh.read(5) == b"%PDF-"
        except OSError:
            return False

    def sheet_names(self, path: str, rules: RuleSet | None = None) -> list[str]:
        if rules is None or rules.pdf is None:
            return []
        return list(_headings(path, Path(path).stat().st_mtime, rules.to_json(indent=None)))

    def parse(self, path: str, label: str, rules: RuleSet) -> Workbook:
        if rules.pdf is None:
            raise UnsupportedFormat(
                f"ruleset {rules.name!r} has no 'pdf' section; it cannot read PDF guides")
        pdfplumber = _pdfplumber()
        warnings: list[Warning_] = []
        with pdfplumber.open(path) as pdf:
            pages = _read_pages(pdf, rules)
            groups: dict[str, list[_Page]] = {}
            for page in pages:
                if page.section is None:
                    policy = rules.sheets.unknown_sheet
                    if policy != "skip":
                        warnings.append(Warning_(
                            kind="unknown_section", severity=policy,
                            message=f"page {page.number}: no section rule matches "
                                    f"{' / '.join(page.heading[:3])!r}",
                            where=f"{label}!p{page.number}"))
                    continue
                name = _sheet_name(page)
                if name:
                    groups.setdefault(name, []).append(page)

            sheets: dict[str, Sheet] = {}
            for name, group in groups.items():
                layout = group[0].section.layout
                if layout == "option_matrix":
                    sheet = _option_matrix(name, group, rules, label, warnings)
                elif layout == "feature_list":
                    sheet = _feature_list(name, group, rules, label, warnings)
                elif layout == "code_grid":
                    sheet = _code_grid(name, group, rules, label, warnings)
                else:
                    sheet = _code_grid(name, group, rules, label, warnings, keyed_by_code=False)
                if not sheet.rows:
                    warnings.append(Warning_(
                        kind="empty_section", severity="warn",
                        message=f"{name}: no rows could be read from pages "
                                f"{', '.join(str(p.number) for p in group)}",
                        where=f"{label}!{name}"))
                    continue
                sheets[name] = sheet

        dictionary = rules.sheets.dictionary_sheet
        if rules.pdf.synthesize_dictionary and dictionary:
            sheets[dictionary] = _dictionary(dictionary, sheets, label, rules)

        book = Workbook(path=path, label=label, sheets=sheets, warnings=warnings)
        idx = book.code_index()
        for sheet in sheets.values():
            sheet.rows = [
                replace(row, appears_on_sheets=frozenset(
                    s for c in row.codes for s in idx.get(c, ())))
                for row in sheet.rows
            ]
        return book


def _dictionary(name: str, sheets: dict[str, Sheet], label: str, rules: RuleSet) -> Sheet:
    """Every single option code in the guide, with its first description."""
    code_rx = re.compile(rules.pdf.code_regex)
    rows: list[Row] = []
    seen: set[str] = set()
    for sheet in sheets.values():
        for row in sheet.rows:
            code = row.orderable_rpo
            if not code or code in seen or not code_rx.fullmatch(code):
                continue
            seen.add(code)
            rows.append(Row(
                sheet=name, section_path=(), occurrence_index=0, description=row.description,
                display_description=row.display_description, values={}, orderable_rpo=code,
                prov=replace(row.prov, sheet=name)))
    return Sheet(name=name, kind="flat_lookup", rows=rows, columns=[])
