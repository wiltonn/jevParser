"""Serialize a comparison into the changeset the UI reads.

The page embeds this whole structure and renders every view from it, which is
what makes tuning free: each weight, threshold and filter the UI offers is
arithmetic over numbers already here, so a slider moves the bands with no
server, no API key and no new inference.

Raw ``score`` / ``probabilities`` / ``confidence`` are kept per question and
never collapsed into a single severity, so a lens added later can reuse them.

Every addressable thing carries a stable id (``eid`` events, ``rid`` rows,
``uid`` residue, ``jid`` identity judgments) so a link survives a rerun.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from ..align.axes import align_axis
from ..align.pairing import BookAlignment
from ..classify import ChangeEvent
from ..judge.run import DEFAULT_LENSES, Judgments
from ..model.canonical import Row, Sheet, Workbook

#: Sheets rendered by the same table shape.  ``sheets`` dispatches on this
#: rather than on the sheet name, so a new nameplate's worksheets work unchanged.
SHAPES = {
    "feature_matrix": "feature",
    "keyed_rows": "keyed",
    "stacked_matrix": "feature",
    "grid": "grid",
}


def _short(*parts: str) -> str:
    return hashlib.blake2b("\0".join(parts).encode("utf-8"),
                           digest_size=4).hexdigest()


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _delta(d) -> dict[str, Any]:
    return {
        "axis": d.axis_id,
        "trim": d.trim,
        "direction": d.direction,
        "from": d.token_a,
        "to": d.token_b,
        "changed": d.changed,
        "added": [c.raw for c in d.constraints.added],
        "dropped": [c.raw for c in d.constraints.removed],
        "reworded": [[a.raw, b.raw] for a, b in d.constraints.rewritten],
        "cell_old": d.prov_a.a1_ref if d.prov_a else None,
        "cell_new": d.prov_b.a1_ref if d.prov_b else None,
    }


def _event(event: ChangeEvent) -> dict[str, Any]:
    return {
        "eid": event.eid,
        "kind": event.kind,
        "sheet": event.sheet,
        "also_on": list(event.also_on),
        "code": event.code,
        "description": event.description,
        "detail": event.detail,
        "disposition": event.disposition,
        "tier": event.tier,
        "order_affecting": event.order_affecting,
        "rows": [list(ref) for ref in event.row_ids],
        "judgments": {
            qid: {
                "score": a.get("score"), "noul": a.get("noul"),
                "choice": a.get("choice"), "confidence": a.get("confidence"),
                "probabilities": a.get("probabilities"),
            }
            for qid, a in (event.judgments or {}).items()
        },
        "deltas": [_delta(d) for d in event.deltas],
    }


def _row(row: Row) -> dict[str, Any]:
    """One worksheet row, with its resolved conditions.

    Conditions are carried in full rather than lazily: all 1,370 rows across
    both years come to well under a megabyte, and search matches condition text,
    which is the whole argument of the tool.
    """
    return {
        "rid": row.rid,
        "code": row.code,
        "orderable": row.orderable_rpo,
        "ref": row.ref_rpo,
        "desc": row.display_description,
        "section": " / ".join(row.section_path) or None,
        "cell": row.prov.a1_ref,
        "v": {
            axis: {
                "t": cell.token.value,
                "val": cell.value,
                "c": [c.raw for c in cell.conditions],
            }
            for axis, cell in row.values.items()
        },
    }


def _axis(entries) -> list[dict[str, Any]]:
    return [{"axis": e.axis_id, "name": e.display_name, "code": e.order_code}
            for e in entries]


def _sheet(name: str, old: Sheet, new: Sheet, alignment: BookAlignment,
           changed: dict[str, set[str]]) -> dict[str, Any]:
    axes = align_axis(old.columns, new.columns)
    sheet_alignment = alignment.sheets.get(name)
    return {
        "name": name,
        "slug": slugify(name),
        "kind": new.kind,
        "shape": SHAPES.get(new.kind, "feature"),
        # Both years: the trim lineup is itself a change when it moves, and the
        # old year's columns are the only way to show a renamed trim.
        "trims": {"old": _axis(old.columns), "new": _axis(new.columns)},
        "axis": {
            "pairs": [[a.axis_id, b.axis_id] for a, b in axes.pairs],
            "added": _axis(axes.added),
            "removed": _axis(axes.removed),
            "renamed": [[a.display_name, b.display_name] for a, b in axes.renamed],
            "changed": axes.changed,
        },
        "rows": {"old": [_row(r) for r in old.rows],
                 "new": [_row(r) for r in new.rows]},
        "counts": {
            "rows_old": len(old.rows),
            "rows_new": len(new.rows),
            "paired": len(sheet_alignment.pairs) if sheet_alignment else 0,
            "changed": len(changed.get(name, ())),
        },
    }


def build(book_a: Workbook, book_b: Workbook, alignment: BookAlignment,
          events: list[ChangeEvent], judgments: Judgments | None) -> dict[str, Any]:
    # rid -> eid, so a row in the sheets view can link to the change it caused.
    row_to_event: dict[str, str] = {}
    changed_by_sheet: dict[str, set[str]] = {}
    for event in events:
        for _year, sheet_name, rid in event.row_ids:
            row_to_event.setdefault(rid, event.eid)
            changed_by_sheet.setdefault(sheet_name, set()).add(rid)

    sheets = []
    for name, new in book_b.sheets.items():
        old = book_a.sheets.get(name)
        if old is None or new.kind == "flat_lookup":
            continue                      # the dictionary is the arbiter, not a view
        sheets.append(_sheet(name, old, new, alignment, changed_by_sheet))

    slugs = [s["slug"] for s in sheets]
    if len(set(slugs)) != len(slugs):
        raise ValueError(f"worksheet slugs are not unique: {sorted(slugs)}")

    for sheet in sheets:
        for year in ("old", "new"):
            for row in sheet["rows"][year]:
                row["eid"] = row_to_event.get(row["rid"])

    tier_counts: dict[str, int] = {}
    for sheet_alignment in alignment.sheets.values():
        for tier, count in sheet_alignment.tier_counts().items():
            tier_counts[str(tier)] = tier_counts.get(str(tier), 0) + count

    residue = [
        {
            "uid": _short(item.sheet, item.year, item.row.code or "",
                          item.row.description),
            "sheet": item.sheet,
            "year": book_a.label if item.year == "a" else book_b.label,
            "code": item.row.code,
            "description": item.row.display_description,
            "disposition": item.disposition,
            "verdict": item.verdict,
        }
        for item in alignment.residue
    ]

    pairings = []
    for key, value in (judgments.pairings if judgments else {}).items():
        answer = value.get("alignment", {})
        pairings.append({
            "jid": _short(key),
            "key": key,
            "outcome": value.get("outcome"),
            "score": answer.get("score"),
            "confidence": answer.get("confidence"),
            "succession": (value.get("succession") or {}).get("noul"),
            "absorbed": (value.get("absorbed") or {}).get("noul"),
        })
    pairings.sort(key=lambda p: -(p["score"] or 0))

    index = book_b.code_index()
    return {
        "old": book_a.label,
        "new": book_b.label,
        "source": {"old": book_a.path, "new": book_b.path},
        "summary": {
            "sheets": len(sheets),
            "rows_old": sum(len(s.rows) for s in book_a.sheets.values()),
            "rows_new": sum(len(s.rows) for s in book_b.sheets.values()),
            "paired": sum(tier_counts.values()),
            "tiers": tier_counts,
            "codes": len(index),
            "multi_sheet_codes": sum(1 for v in index.values() if len(v) > 1),
            "requests": judgments.usage.get("requests", 0) if judgments else 0,
            "input_tokens": judgments.usage.get("input_tokens", 0) if judgments else 0,
            "cache_hits": judgments.cache_hits if judgments else 0,
            "residue": len(residue),
            "judged": len(pairings),
            "needs_review": sum(1 for r in residue
                                if r["disposition"] == "needs_review"),
        },
        "lenses": {
            name: {"weights": lens.weights, "bands": lens.bands,
                   "confidence_floor": lens.confidence_floor}
            for name, lens in DEFAULT_LENSES.items()
        },
        "sheets": sheets,
        "events": [_event(e) for e in events],
        "residue": residue,
        "pairings": pairings,
        "warnings": [
            {"kind": w.kind, "severity": w.severity, "where": w.where,
             "message": w.message}
            for w in (book_a.warnings + book_b.warnings)
        ],
    }
