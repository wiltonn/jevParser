"""The FastAPI application: JSON API under /api, the React app at /."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .db import configure, session_scope

log = logging.getLogger("jev_web")


def _domain_errors() -> tuple[type[Exception], ...]:
    from .services.comparisons import ComparisonError
    from .services.documents import DocumentError
    from .services.exports import ExportError
    from .services.rulesets import RulesetError

    return ComparisonError, DocumentError, ExportError, RulesetError


def prepare(settings: Settings) -> None:
    """Migrate, and seed on first run."""
    from . import migrate
    from .services.seed import seed

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    migrate.upgrade(settings.db_url)
    configure(settings.db_url)
    if settings.auto_seed:
        with session_scope() as session:
            seed(session)


def create_app(settings: Settings | None = None, *, start_workers: bool = True) -> FastAPI:
    settings = settings or get_settings()
    pool = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal pool
        prepare(settings)
        if start_workers and settings.job_mode == "threads" and settings.workers > 0:
            from .jobs.worker import WorkerPool

            pool = WorkerPool(settings.workers)
            pool.start()
        yield
        if pool is not None:
            pool.stop()

    app = FastAPI(title="jev-diff", version="0.2.0", lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.exception_handler(ValueError)
    async def _domain(request: Request, exc: ValueError):
        if isinstance(exc, _domain_errors()):
            return JSONResponse({"detail": str(exc)}, status_code=400)
        raise exc

    from .api import admin, catalog, comparisons, documents, jobs, rulesets

    for module in (catalog, documents, comparisons, rulesets, jobs, admin):
        app.include_router(module.router, prefix="/api")

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/design/tokens.css")
    def tokens_css():
        from jev_diff.render import tokens

        return PlainTextResponse(tokens.css(), media_type="text/css")

    dist = Path(settings.web_dist)
    if (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "not found"}, status_code=404)
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app
