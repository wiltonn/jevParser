"""Content-addressed file storage.

A local directory, or Supabase Storage when ``JEV_SUPABASE_URL`` and
``JEV_SUPABASE_SERVICE_KEY`` are set.  Either way callers put a stream and get
a local path back, keyed by sha256; the Supabase store keeps the local
directory as a download cache.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import Blob

_CHUNK = 1 << 20


class LocalBlobStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _key(self, sha: str) -> str:
        return f"{sha[:2]}/{sha}"

    def _local(self, sha: str) -> Path:
        return self.root / self._key(sha)

    def path(self, sha: str) -> Path:
        return self._local(sha)

    def put_stream(self, stream: BinaryIO) -> tuple[str, int, str]:
        """Store a stream; returns ``(sha256, size, storage_key)``.  Idempotent."""
        digest = hashlib.sha256()
        size = 0
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as out:
                while chunk := stream.read(_CHUNK):
                    digest.update(chunk)
                    size += len(chunk)
                    out.write(chunk)
            sha = digest.hexdigest()
            dest = self._local(sha)
            if dest.exists():
                os.unlink(tmp)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return sha, size, self._key(sha)

    def put_bytes(self, data: bytes) -> tuple[str, int, str]:
        import io

        return self.put_stream(io.BytesIO(data))


class SupabaseBlobStore(LocalBlobStore):
    """Objects in a Supabase Storage bucket; ``root`` is a local read cache."""

    def __init__(self, root: Path, url: str, key: str, bucket: str):
        super().__init__(root)
        self.base = f"{url.rstrip('/')}/storage/v1"
        self.key = key
        self.bucket = bucket

    def _request(self, method: str, path: str, data: bytes | None = None,
                 headers: dict[str, str] | None = None):
        req = urllib.request.Request(
            f"{self.base}/{path}", data=data, method=method,
            headers={"apikey": self.key, "Authorization": f"Bearer {self.key}", **(headers or {})})
        return urllib.request.urlopen(req, timeout=120)

    def _ensure_bucket(self) -> None:
        if self.bucket in _buckets_ready:
            return
        import json

        body = json.dumps({"id": self.bucket, "name": self.bucket, "public": False}).encode()
        try:
            self._request("POST", "bucket", body, {"Content-Type": "application/json"}).close()
        except urllib.error.HTTPError as exc:
            # Already exists: 409, or 400 with a "Duplicate" body on older servers.
            if exc.code not in (400, 409):
                raise
        _buckets_ready.add(self.bucket)

    def put_stream(self, stream: BinaryIO) -> tuple[str, int, str]:
        sha, size, key = super().put_stream(stream)
        self._ensure_bucket()
        with open(self._local(sha), "rb") as f:
            data = f.read()
        self._request("POST", f"object/{self.bucket}/{key}", data,
                      {"Content-Type": "application/octet-stream", "x-upsert": "true"}).close()
        return sha, size, key

    def path(self, sha: str) -> Path:
        dest = self._local(sha)
        if dest.exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".download-")
        try:
            with os.fdopen(fd, "wb") as out, \
                    self._request("GET", f"object/{self.bucket}/{self._key(sha)}") as resp:
                shutil.copyfileobj(resp, out, _CHUNK)
            os.replace(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return dest


_buckets_ready: set[str] = set()


def store() -> LocalBlobStore:
    settings = get_settings()
    if settings.supabase_url and settings.supabase_service_key:
        return SupabaseBlobStore(settings.blob_dir, settings.supabase_url,
                                 settings.supabase_service_key, settings.supabase_bucket)
    return LocalBlobStore(settings.blob_dir)


def save_blob(session: Session, stream: BinaryIO, mime: str) -> Blob:
    sha, size, key = store().put_stream(stream)
    blob = session.get(Blob, sha)
    if blob is None:
        blob = Blob(sha256=sha, size=size, mime=mime, storage_key=key)
        session.add(blob)
        session.flush()
    return blob


def save_bytes(session: Session, data: bytes, mime: str) -> Blob:
    import io

    return save_blob(session, io.BytesIO(data), mime)


def blob_path(sha: str) -> Path:
    return store().path(sha)
