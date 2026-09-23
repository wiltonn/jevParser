"""Generate the report's `:root` custom properties from the design system tokens.

The system's own `tokens.css` is written by its page and is **not** published --
only `tokens.json` is -- so a consumer generates the variables itself. Doing it
from the token file rather than hand-copying values is the point: the vendored
`tokens.json` stays the single definition, and `tests/test_design.py` fails if
the emitted report and the vendored files disagree.

Light is the default. Dark follows `prefers-color-scheme` unless `html[data-theme]`
pins one, which is what the theme button toggles.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DESIGN = Path(__file__).parent / "design"

#: Families whose tokens carry one value per theme.
THEMED = ("color", "shadow")
#: Families with a single value, emitted once.
FLAT = ("spacing", "radius", "opacity", "layer")


def load() -> dict[str, Any]:
    return json.loads((DESIGN / "tokens.json").read_text(encoding="utf-8"))


def _value(raw: Any, theme: str, first: str) -> str | None:
    """A token's value for one theme.

    A plain string belongs to the first theme; a token with no value for a theme
    inherits the first theme's, which is why the primary theme is listed first.
    """
    if isinstance(raw, dict):
        return raw.get(theme, raw.get(first))
    return raw if theme == first else raw


def _declarations(tokens: dict[str, Any], theme: str, first: str) -> list[str]:
    out: list[str] = []
    for family in THEMED:
        for token in tokens.get(family, {}).get("tokens", []):
            value = _value(token["value"], theme, first)
            if value is not None:
                out.append(f"--{token['name']}:{value}")
    return out


def _flat(tokens: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for family in FLAT:
        for token in tokens.get(family, {}).get("tokens", []):
            out.append(f"--{token['name']}:{token['value']}")
    for name, stack in tokens.get("type", {}).get("families", {}).items():
        out.append(f"--font-{name}:{stack}")
    return out


def css(tokens: dict[str, Any] | None = None) -> str:
    """The `:root` blocks: light, then dark twice (media query and attribute)."""
    tokens = tokens or load()
    themes = [t["id"] for t in tokens["color"]["themes"]] or ["light"]
    first = themes[0]

    light = _declarations(tokens, first, first) + _flat(tokens)
    blocks = [":root{" + ";".join(light) + "}"]

    for theme in themes[1:]:
        dark = _declarations(tokens, theme, first)
        if not dark:
            continue
        body = ";".join(dark)
        blocks.append(
            f"@media(prefers-color-scheme:{theme})"
            f'{{:root:not([data-theme="{first}"]){{{body}}}}}'
        )
        blocks.append(f':root[data-theme="{theme}"]{{{body}}}')
    return "\n".join(blocks) + "\n"


def bundle_css() -> str:
    return (DESIGN / "bundle.css").read_text(encoding="utf-8")


def bundle_js() -> str:
    return (DESIGN / "bundle.js").read_text(encoding="utf-8")


def version() -> str:
    for line in (DESIGN / "VERSION").read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip()
    return "unknown"
