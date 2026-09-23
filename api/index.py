"""Vercel entrypoint: exposes the FastAPI app as a module-level ``app``."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Vercel's filesystem is read-only except /tmp, and a function is frozen
# between requests, so jobs run inside the request that drains them.
if os.environ.get("VERCEL"):
    os.environ.setdefault("JEV_DATA_DIR", "/tmp/jev")
    os.environ.setdefault("JEV_JOB_MODE", "request")
    # Built by [tool.vercel.scripts] build in pyproject.toml.
    os.environ.setdefault("JEV_WEB_DIST", str(ROOT / "web" / "dist"))

from jev_web.app import create_app  # noqa: E402

app = create_app()
