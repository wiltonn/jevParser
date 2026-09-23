"""Every pipeline stage must match the fingerprints taken before the refactor.

See ``tests/snapshot.py``.  ``cache_misses == 0`` is the sharpest check here:
question wording and request state are part of every judgment cache key, so a
single changed character in a templated question would re-bill the whole run.
"""

import json

import pytest

from snapshot import GOLDEN, compute


@pytest.fixture(scope="module")
def snapshot():
    return compute()


@pytest.mark.parametrize("stage", [
    "book_2026", "book_2027", "alignment", "events", "event_count",
    "payload_unjudged", "cache_keys", "cache_lookups", "payload_judged",
    "report_html",
])
def test_stage_matches_golden(snapshot, stage):
    golden = json.loads(GOLDEN.read_text())
    assert snapshot[stage] == golden[stage], f"{stage} drifted from the golden"


def test_every_judgment_is_a_cache_hit(snapshot):
    assert snapshot["cache_misses"] == 0
