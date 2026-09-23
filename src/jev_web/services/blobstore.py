"""Content-addressed file storage.

Local directory now; the interface (put a stream, get a path, keyed by sha256)
is what an object-store backend would implement for the server deployment.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
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

    def path(self, sha: str) -> Path:
        return self.root / self._key(sha)

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
            dest = self.path(sha)
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


def store() -> LocalBlobStore:
    return LocalBlobStore(get_settings().blob_dir)


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
