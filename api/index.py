"""Vercel entrypoint: exposes the FastAPI app as a module-level ``app``."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Vercel's filesystem is read-only except /tmp.
if os.environ.get("VERCEL"):
    os.environ.setdefault("JEV_DATA_DIR", "/tmp/jev")

from jev_web.app import create_app  # noqa: E402

app = create_app()
