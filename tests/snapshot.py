"""Fingerprints of every pipeline stage, for refactor parity.

Each stage is serialized deterministically and hashed.  ``tests/golden`` holds
the hashes taken before rules moved from module constants into data, so any
drift in parsing, pairing, classification, the judgment questions (whose wording
is part of every cache key) or the rendered payload fails loudly.

Regenerate only on an intentional behaviour change:
``python tests/snapshot.py --write``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

GOLDEN = ROOT / "tests" / "golden" / "parity.json"
CACHE = ROOT / ".jev-cache" / "judgments.json"
GMC = ROOT / "docs" / "content" / "GMC"
BOOKS = {
    "2026": GMC / "2026 GMC Yukon _ Denali Export.xlsx",
    "2027": GMC / "2027 GMC Yukon Export.xlsx",
}


def _encode(obj):
    if isinstance(obj, (frozenset, set)):
        return sorted(obj, key=str)
    raise TypeError(f"not serializable: {type(obj).__name__}")


def _hash(value) -> str:
    text = json.dumps(value, sort_keys=True, default=_encode, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _book(book) -> dict:
    return {
        "label": book.label,
        "sheets": {
            name: {
                "kind": s.kind,
                "columns": [dataclasses.asdict(c) for c in s.columns],
                "footnotes": s.footnotes,
                "legend": dataclasses.asdict(s.legend) if s.legend else None,
                "images": [dataclasses.asdict(i) for i in s.images],
                "rows": [dataclasses.asdict(r) | {"rid": r.rid} for r in s.rows],
            }
            for name, s in book.sheets.items()
        },
        "warnings": [dataclasses.asdict(w) for w in book.warnings],
    }


class _RecordingCache:
    """Read-only view of the committed cache that records every lookup."""

    def __init__(self, inner):
        self.inner = inner
        self.keys: list[str] = []

    @property
    def hits(self):
        return self.inner.hits

    @property
    def misses(self):
        return self.inner.misses

    def get(self, state, qid, question):
        self.keys.append(self.inner.key(state, qid, question))
        return self.inner.get(state, qid, question)

    def put(self, *args):
        pass

    def save(self):
        pass

    key = staticmethod(lambda *a: _RecordingCache.inner_key(*a))


def compute() -> dict[str, object]:
    from jev_diff.align.pairing import align_books
    from jev_diff.classify import build_events, consolidate
    from jev_diff.judge import client as client_mod
    from jev_diff.judge import run as run_mod
    from jev_diff.judge.cache import JudgmentCache
    from jev_diff.parse.registry import parse_workbook
    from jev_diff.render.html import render
    from jev_diff.render.payload import build

    a = parse_workbook(str(BOOKS["2026"]), "2026")
    b = parse_workbook(str(BOOKS["2027"]), "2027")
    alignment = align_books(a, b)
    events = consolidate(build_events(a, b, alignment))

    out: dict[str, object] = {
        "book_2026": _hash(_book(a)),
        "book_2027": _hash(_book(b)),
        "alignment": _hash({
            "sheets": {
                n: {"pairs": [[p.a.rid, p.b.rid, p.tier, p.reason] for p in s.pairs],
                    "only_a": [r.rid for r in s.only_a],
                    "only_b": [r.rid for r in s.only_b]}
                for n, s in alignment.sheets.items()
            },
            "residue": [[u.row.rid, u.year, u.sheet, u.verdict, u.disposition]
                        for u in alignment.residue],
        }),
        "events": _hash([dataclasses.asdict(e) | {"order_affecting": e.order_affecting}
                         for e in events]),
        "event_count": len(events),
    }
    payload = build(a, b, alignment, events, None)
    payload["source"] = {}
    out["payload_unjudged"] = _hash(payload)

    # Judged run, entirely from the committed cache.  Any miss means a question's
    # wording or state drifted, which would re-bill every judgment.
    def _no_network(requests, **_kw):
        requests = list(requests)
        return [client_mod.Reply(r.key, {}, {}, error="offline") for r in requests]

    saved = client_mod.ask_many, getattr(run_mod, "ask_many", None)
    client_mod.ask_many = _no_network
    if saved[1] is not None:
        run_mod.ask_many = _no_network
    try:
        inner = JudgmentCache(CACHE)
        rec = _RecordingCache(inner)
        _RecordingCache.inner_key = staticmethod(inner.key)
        judgments = run_mod.run(alignment, events, rec, workers=1)
    finally:
        client_mod.ask_many = saved[0]
        if saved[1] is not None:
            run_mod.ask_many = saved[1]

    out["cache_keys"] = _hash(sorted(rec.keys))
    out["cache_lookups"] = len(rec.keys)
    out["cache_misses"] = inner.misses
    judged = build(a, b, alignment, events, judgments)
    judged["source"] = {}
    out["payload_judged"] = _hash(judged)
    out["report_html"] = hashlib.sha256(render(judged).encode("utf-8")).hexdigest()
    return out


if __name__ == "__main__":
    result = compute()
    print(json.dumps(result, indent=2))
    if "--write" in sys.argv:
        GOLDEN.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(f"wrote {GOLDEN}", file=sys.stderr)
