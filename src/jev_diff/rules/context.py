"""The comparison a ruleset is being applied to.

Question wording names the two model years and the manufacturer; request state
names the years in its field keys.  Both are rendered from templates against
this context, so one ruleset serves every year pair in its range.
"""

from __future__ import annotations

from dataclasses import dataclass


class _Strict(dict):
    def __missing__(self, key: str) -> str:
        raise KeyError(f"unknown placeholder {{{key}}}")


@dataclass(frozen=True, slots=True)
class PromptContext:
    old_year: str
    new_year: str
    #: The manufacturer as the questions name it, e.g. "GM".
    oem: str = "GM"
    brand: str = ""
    model: str = ""

    def values(self) -> dict[str, str]:
        return {
            "old_year": self.old_year, "new_year": self.new_year,
            "oem": self.oem, "brand": self.brand, "model": self.model,
        }

    def render(self, template: str, **extra: str) -> str:
        return template.format_map(_Strict(self.values() | extra))


#: The comparison the rules were originally written for.
DEFAULT_CONTEXT = PromptContext(old_year="2026", new_year="2027", oem="GM",
                                brand="GMC", model="Yukon")
