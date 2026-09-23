"""Render the changeset as one self-contained HTML file.

The page is a shell: it inlines the design system's tokens, stylesheet and
component bundle, then this project's own stylesheet and application code, and
embeds the changeset. Everything is drawn in the browser, which is what makes
tuning instant -- a weight or a threshold is arithmetic over judgments already
in the page, so the bands re-rank with no server, no API key and no new
inference.

Nothing is fetched. The file opens from ``file://`` with no network at all,
because the data it carries is proprietary and never leaves the machine.

Appearance belongs to the design system (vendored under ``design/``); behaviour
-- the router, the lens arithmetic, search and filtering -- belongs here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import tokens

ASSETS = Path(__file__).parent / "assets"


def _asset(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def _embed(payload: dict[str, Any]) -> str:
    """JSON for a ``<script>`` block.

    ``</script>`` anywhere in the data would end the block early and break the
    page, and a lone ``<!--`` would open an HTML comment, so both are escaped
    into their unicode forms. JSON reads them as the same characters; the HTML
    parser never sees them.
    """
    return (
        json.dumps(payload)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def stylesheet() -> str:
    """Tokens generated from the design system, then its bundle, then ours."""
    return "\n".join((tokens.css(), tokens.bundle_css()))


def script() -> str:
    return "\n".join((tokens.bundle_js(), _asset("app.js")))


def render(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{payload['old']} → {payload['new']} order guide</title>
<style>{stylesheet()}</style></head><body><div class="wrap">

<h1>{payload['old']} → {payload['new']} order guide</h1>
<p class="sub">{len(payload['events'])} changes across {summary['sheets']}
worksheets. Availability and conditions are compared after resolving footnotes,
so renumbering never reads as a change. <button id="theme">theme</button></p>

<div id="app"></div>

<script>window.__CHANGESET__ = {_embed(payload)};</script>
<script>{script()}</script>
</div></body></html>
"""
