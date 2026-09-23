"""The design system is the source of truth for appearance.

These tests fail if the repo and the artifact drift apart, or if the report
stops being self-contained. Appearance lives in the vendored snapshot under
`render/design/`; behaviour lives in `render/assets/`. Nothing may hand-copy a
token value.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from jev_diff.render import html, tokens

DESIGN = Path(tokens.__file__).parent / "design"


def test_vendored_snapshot_is_present_and_stamped():
    for name in ("VERSION", "tokens.json", "bundle.css", "bundle.js"):
        assert (DESIGN / name).exists(), name
    assert tokens.version() == "4"


def test_report_inlines_exactly_the_vendored_bundle():
    """The drift check. If someone edits the report's CSS by hand instead of
    re-syncing the design system, this fails."""
    sheet = html.stylesheet()
    assert tokens.bundle_css() in sheet
    assert tokens.css() in sheet

    script = html.script()
    assert tokens.bundle_js() in script


def test_every_token_used_by_our_css_is_defined_by_the_system():
    """No hand-copied values, and no reference to a token the system dropped."""
    data = tokens.load()
    defined = {
        t["name"]
        for family, block in data.items()
        if isinstance(block, dict) and "tokens" in block
        for t in block["tokens"]
    }
    defined |= {f"font-{k}" for k in data["type"]["families"]}

    bundle = tokens.bundle_css()
    used = set(re.findall(r"var\(--([a-z-]+)\)", bundle))
    assert used <= defined, f"undefined tokens: {sorted(used - defined)}"
    assert used, "the stylesheet should be built from tokens, not literals"


def test_the_ui_components_declare_no_literal_colours():
    """Colour belongs to the tokens. `color-mix` over a token is allowed; a hex
    literal in a component is not -- the one exception the system records is the
    hard-coded #fff on the selected lens button, which predates this."""
    bundle = tokens.bundle_css()
    ours = bundle[bundle.index("jev-diff UI components"):]
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", ours)


def test_generated_tokens_cover_both_themes():
    css = tokens.css()
    assert css.startswith(":root{")
    assert "@media(prefers-color-scheme:dark)" in css
    assert ':root[data-theme="dark"]' in css
    # Light is the default and must not sit behind a media query.
    assert css.index(":root{") < css.index("@media")


def test_report_is_self_contained(tmp_path, books, alignment, events):
    """One file, no network. The data is proprietary and never leaves the
    machine, so a fetched stylesheet or font would be a leak as well as a
    broken offline file."""
    from jev_diff.render.payload import build

    payload = build(books["2026"], books["2027"], alignment, events, None)
    page = html.render(payload)

    assert "http://" not in page and "https://" not in page
    assert page.count("<script>") == 2          # changeset, then behaviour
    assert "</script>" not in json.dumps(payload)


def test_changeset_embedding_escapes_script_terminators():
    payload = {"events": [], "summary": {"sheets": 0},
               "old": "a", "new": "b",
               "x": "</script><script>alert(1)</script>"}
    page = html.render(payload)
    body = page[page.index("__CHANGESET__"):]
    assert "</script><script>alert" not in body
    assert "\\u003c/script" in body


def test_the_bundle_holds_components_only():
    """A regression guard. When the UI components were first extracted, the
    slice ran past them and carried jev-diff's lens arithmetic and router state
    into the design system's bundle -- behaviour published as if it were
    appearance. The bundle renders; it must not decide anything."""
    bundle = tokens.bundle_js()
    for component in ("Nav", "FilterBar", "Crumb", "SheetTable",
                      "KeyedTable", "GridTable", "AxisLineup"):
        assert f"function {component}(" in bundle, component
    for behaviour in ("applyLens", "bandOf", "lensValue", "stateParams",
                      "readParams", "pushState", "matches", "haystack"):
        assert f"function {behaviour}(" not in bundle, (
            f"{behaviour} is the consumer's, not the design system's")

    app = (Path(tokens.__file__).parent / "assets" / "app.js").read_text()
    for behaviour in ("applyLens", "bandOf", "stateParams"):
        assert f"function {behaviour}(" in app, behaviour


def test_web_app_tokens_match_design_system():
    """The React app's generated tokens must be today's tokens.json.

    Regenerate with ``npm --prefix web run design``.
    """
    generated = Path(__file__).resolve().parents[1] / "web" / "src" / "design" / "tokens.gen.css"
    assert generated.read_text(encoding="utf-8") == tokens.css()
