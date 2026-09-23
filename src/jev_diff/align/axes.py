"""Align an axis (columns, or keyed rows) between two model years.

Cells must never be compared positionally.  A trim inserted, removed or
reordered would otherwise produce a wall of spurious deltas, and the Wheels
sheet already shifts its columns relative to every other feature sheet.

The order code is the stable identity -- ``5SA1`` is what gets typed into the
order system -- so it wins over the display name, which GM does rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model.canonical import AxisEntry
from ..parse.footnotes import normalize_label


@dataclass(slots=True)
class AxisAlignment:
    """The result of aligning two axes.

    ``incomparable`` is the third cell state the report needs: a column present
    in only one year has no counterpart, which is not the same as unchanged.
    """

    pairs: list[tuple[AxisEntry, AxisEntry]] = field(default_factory=list)
    added: list[AxisEntry] = field(default_factory=list)
    removed: list[AxisEntry] = field(default_factory=list)
    renamed: list[tuple[AxisEntry, AxisEntry]] = field(default_factory=list)
    reordered: list[tuple[AxisEntry, AxisEntry]] = field(default_factory=list)

    @property
    def paired_ids(self) -> list[tuple[str, str]]:
        return [(a.axis_id, b.axis_id) for a, b in self.pairs]

    @property
    def union_ids(self) -> list[str]:
        """Every axis id in either year, old-only columns kept in place."""
        out = [b.axis_id for _, b in self.pairs]
        out += [c.axis_id for c in self.added if c.axis_id not in out]
        out += [c.axis_id for c in self.removed if c.axis_id not in out]
        return out

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed or self.renamed)


def align_axis(a: list[AxisEntry], b: list[AxisEntry]) -> AxisAlignment:
    """Align by order code, then exact display name, then normalized name."""
    result = AxisAlignment()
    left, right = list(a), list(b)

    def take(pred_a, pred_b) -> list[tuple[AxisEntry, AxisEntry]]:
        found = []
        for x in list(left):
            match = next((y for y in right if pred_a(x) is not None
                          and pred_a(x) == pred_b(y)), None)
            if match is not None:
                found.append((x, match))
                left.remove(x)
                right.remove(match)
        return found

    matched = take(lambda e: e.order_code, lambda e: e.order_code)
    matched += take(lambda e: e.display_name, lambda e: e.display_name)
    matched += take(lambda e: normalize_label(e.display_name),
                    lambda e: normalize_label(e.display_name))

    for x, y in matched:
        result.pairs.append((x, y))
        if normalize_label(x.display_name) != normalize_label(y.display_name):
            # Same order code, different name: a rename, and itself an event.
            result.renamed.append((x, y))
        if x.index != y.index:
            result.reordered.append((x, y))

    result.removed.extend(left)
    result.added.extend(right)
    # Keep pairs in the newer year's column order so the matrix reads naturally.
    result.pairs.sort(key=lambda p: p[1].index)
    return result
