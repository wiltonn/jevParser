"""The contract a source format implements.

Everything downstream of parsing -- alignment, classification, judgment,
rendering -- reads only the canonical :class:`~jev_diff.model.canonical.Workbook`,
so supporting a new format means writing one adapter and nothing else.
"""

from __future__ import annotations

from typing import Protocol

from ..model.canonical import Workbook
from ..rules.model import RuleSet


class UnsupportedFormat(ValueError):
    pass


class FormatAdapter(Protocol):
    #: Matches ``RuleSet.format`` and the document's stored format.
    format: str
    #: File extensions this adapter claims, lower-case with the dot.
    extensions: tuple[str, ...]

    def sniff(self, path: str) -> bool:
        """Whether the file's content is this format, regardless of its name."""
        ...

    def sheet_names(self, path: str, rules: RuleSet | None = None) -> list[str]:
        """Sheet names the file would produce, for detection, without a full parse.

        A PDF has no sheets of its own; its names come from the ruleset's
        section map, so the caller passes the ruleset being tried."""
        ...

    def parse(self, path: str, label: str, rules: RuleSet) -> Workbook:
        ...
