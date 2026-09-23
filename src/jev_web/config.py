"""Settings, from the environment (``JEV_*``) or a ``.env`` file.

Local by default: SQLite and blobs under ``./data``.  Pointing
``JEV_DATABASE_URL`` at Postgres is the whole server migration for the data
layer; blob storage sits behind ``services.blobstore`` for the same reason.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JEV_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    database_url: str | None = None
    #: Background job threads.  Keep 1 on SQLite: it serialises writers anyway.
    workers: int = 1
    #: Serve the built React app from here when it exists.
    web_dist: Path = Path("web/dist")
    #: Seed the catalog and the GM ruleset on first start.
    auto_seed: bool = True
    #: ``threads``: worker threads drain the queue inside the process.
    #: ``request``: no threads; ``POST /api/jobs/drain`` runs a job to
    #: completion inside that request (serverless hosts that freeze between
    #: requests).
    job_mode: Literal["threads", "request"] = "threads"
    #: Blob storage in Supabase Storage when both are set; local otherwise.
    supabase_url: str | None = None
    supabase_service_key: str | None = None
    supabase_bucket: str = "blobs"

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'jev.db').resolve()}"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def secret_key_path(self) -> Path:
        return self.data_dir / "secret.key"


@lru_cache
def get_settings() -> Settings:
    return Settings()
