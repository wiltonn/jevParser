"""Write an annotated copy of the newer workbook.

**V1 limitation -- embedded images and drawings are not preserved.**  openpyxl
does not read them, so anything it re-saves loses them; on the GMC exports that
means the 18 wheel photographs anchored to the Wheels sheet.  Preserving them
needs byte-level patching of the xlsx zip (rewriting ``xl/styles.xml`` and the
``s=`` attributes while streaming every other part through untouched), which is
deliberately out of scope for V1.  The run warns when a workbook contains media
so the loss is never silent.

What the annotated copy does give: every changed cell filled by direction, a
legend, and a prepended Change Log listing each change against its cell
reference.
"""

from __future__ import annotations

import zipfile

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..classify import ChangeEvent

#: Fill per rule-derived direction.  Colour encodes *direction*; severity is the
#: report's job, because severity depends on a lens and this file has none.
FILLS = {
    "content_added": "C6E9D0",      # green   -- newly available
    "content_removed": "F3C9C0",    # red     -- no longer available
    "upgraded": "C2E3EA",           # teal    -- easier to get
    "decontented": "F5DCC0",        # orange  -- harder to get
    "package_shift": "DDD0EE",      # purple  -- bundled differently
    "fulfillment_shift": "E4DCF2",  # violet  -- obtained differently
    "constraint_only": "F7E9C0",    # amber   -- conditions changed
    "incomparable": "E0E0DC",       # grey    -- no counterpart this year
}

HEADER = ("Band", "Score", "Kind", "Code", "Sheet", "Cell", "Trim",
          "Change", "Description", "Detail")


def _fill(colour: str) -> PatternFill:
    return PatternFill("solid", start_color=colour, end_color=colour)


def media_count(path: str) -> int:
    """Images embedded in the source workbook, which the copy will not keep."""
    with zipfile.ZipFile(path) as z:
        return len([n for n in z.namelist() if n.startswith("xl/media/")])


def annotate(source: str, out: str, events: list[ChangeEvent], *,
             bands: dict[int, str] | None = None,
             values: dict[int, float] | None = None) -> dict[str, int]:
    """Copy ``source`` to ``out`` with changed cells filled and a Change Log."""
    workbook = load_workbook(source)
    bands = bands or {}
    values = values or {}

    log = workbook.create_sheet("Change Log", 0)
    log.sheet_properties.tabColor = "8A6212"
    for col, title in enumerate(HEADER, start=1):
        cell = log.cell(1, col, title)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="top")
    log.freeze_panes = "A2"

    row = 2
    painted = 0
    for index, event in enumerate(events):
        band = bands.get(index, "")
        value = values.get(index)
        wrote_event_row = False

        for delta in event.deltas:
            if not delta.changed or delta.prov_b is None:
                continue
            target = workbook[event.sheet] if event.sheet in workbook else None
            if target is not None:
                try:
                    target[delta.prov_b.a1_ref].fill = _fill(
                        FILLS.get(delta.direction, "EDEDE8")
                    )
                    painted += 1
                except (ValueError, KeyError):
                    pass
            change = delta.direction.replace("_", " ")
            detail = "; ".join(
                [f"- {c.raw}" for c in delta.constraints.removed]
                + [f"+ {c.raw}" for c in delta.constraints.added]
                + [f"~ {a.raw} -> {b.raw}" for a, b in delta.constraints.rewritten]
            )
            _write(log, row, (
                band, value, event.kind.replace("_", " "), event.code or "",
                event.sheet, delta.prov_b.a1_ref if delta.prov_b else "",
                delta.trim, f"{delta.token_a or '-'} -> {delta.token_b or '-'} "
                            f"({change})", event.description, detail,
            ))
            row += 1
            wrote_event_row = True

        if not wrote_event_row:
            # Row-level changes (added, retired, moved between sheets) have no
            # single cell to point at, but still belong in the log.
            _write(log, row, (
                band, value, event.kind.replace("_", " "), event.code or "",
                event.sheet, "", "", event.disposition or "",
                event.description, event.detail,
            ))
            row += 1

    _legend(log, row + 1)
    for column, width in zip("ABCDEFGHIJ", (11, 7, 14, 8, 22, 8, 16, 34, 52, 64)):
        log.column_dimensions[column].width = width

    # openpyxl re-embeds images only when Pillow happens to be installed (the
    # optional ``pdf`` extra brings it), and then not faithfully -- 17 source
    # photographs came back as 18 parts.  Drop them explicitly so the output
    # does not depend on what else is installed; see the V1 note above.
    for sheet in workbook.worksheets:
        sheet._images = []
    workbook.save(out)
    return {"log_rows": row - 2, "cells_filled": painted}


def _write(sheet, row: int, values: tuple) -> None:
    for col, value in enumerate(values, start=1):
        cell = sheet.cell(row, col, value)
        cell.alignment = Alignment(vertical="top", wrap_text=col >= 8)


def _legend(sheet, row: int) -> None:
    sheet.cell(row, 1, "Legend").font = Font(bold=True)
    for offset, (direction, colour) in enumerate(FILLS.items(), start=1):
        sheet.cell(row + offset, 1).fill = _fill(colour)
        sheet.cell(row + offset, 2, direction.replace("_", " "))
