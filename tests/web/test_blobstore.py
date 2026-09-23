"""The Supabase Storage blob backend, against a fake Storage server."""

from __future__ import annotations

import hashlib
import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture
def storage():
    objects: dict[str, bytes] = {}
    buckets: set[str] = set()
    seen: list[tuple[str, str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def _reply(self, code: int, body: bytes = b"{}"):
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            seen.append(("POST", self.path, self.headers.get("apikey")))
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/storage/v1/bucket":
                if "blobs" in buckets:
                    return self._reply(409)
                buckets.add("blobs")
                return self._reply(200)
            objects[self.path.removeprefix("/storage/v1/object/")] = body
            self._reply(200)

        def do_GET(self):
            seen.append(("GET", self.path, self.headers.get("apikey")))
            data = objects.get(self.path.removeprefix("/storage/v1/object/"))
            if data is None:
                return self._reply(404)
            self._reply(200, data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", objects, seen
    server.shutdown()


def test_put_uploads_and_path_downloads_into_a_cold_cache(tmp_path, storage):
    from jev_web.services import blobstore

    url, objects, seen = storage
    blobstore._buckets_ready.clear()
    data = b"order guide bytes"
    sha = hashlib.sha256(data).hexdigest()

    writer = blobstore.SupabaseBlobStore(tmp_path / "a", url, "secret", "blobs")
    assert writer.put_stream(io.BytesIO(data)) == (sha, len(data), f"{sha[:2]}/{sha}")
    assert objects[f"blobs/{sha[:2]}/{sha}"] == data
    assert all(key == "secret" for _, _, key in seen)

    # Another instance with an empty cache (a fresh serverless container).
    reader = blobstore.SupabaseBlobStore(tmp_path / "b", url, "secret", "blobs")
    path = reader.path(sha)
    assert path.read_bytes() == data
    gets = sum(1 for method, _, _ in seen if method == "GET")
    reader.path(sha)
    assert sum(1 for method, _, _ in seen if method == "GET") == gets  # served from cache


def test_existing_bucket_is_fine(tmp_path, storage):
    from jev_web.services import blobstore

    url, _, _ = storage
    blobstore._buckets_ready.clear()
    blobstore.SupabaseBlobStore(tmp_path, url, "k", "blobs").put_bytes(b"one")
    blobstore._buckets_ready.clear()
    blobstore.SupabaseBlobStore(tmp_path, url, "k", "blobs").put_bytes(b"two")  # 409 on bucket
