"""Write web/src/lib/lens.fixtures.json from the Python lens implementation."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

import snapshot  # noqa: E402
from jev_diff.judge.cache import JudgmentCache  # noqa: E402
from jev_diff.judge.run import Lens, band, lens_value, lenses_from  # noqa: E402
from jev_diff.pipeline import compare  # noqa: E402


class ReadOnly(JudgmentCache):
    def save(self):
        pass


result = compare(str(snapshot.BOOKS["2026"]), str(snapshot.BOOKS["2027"]),
                 label_a="2026", label_b="2027", judge=True, store=ReadOnly(snapshot.CACHE))
lenses = dict(lenses_from())
# Off-default settings too, so the port is checked beyond the defaults.
lenses["skewed"] = Lens("skewed", {"order_risk": 0.3, "content_churn": 0.9},
                        {"material": 1.2, "review": 0.3}, 0.9)
cases = []
for e in result.events:
    judgments = {q: {"score": a.get("score"), "confidence": a.get("confidence")}
                 for q, a in e.judgments.items()}
    cases.append({
        "eid": e.eid, "judgments": judgments,
        "expected": {name: {"value": lens_value(e, lens), "band": band(e, lens)}
                     for name, lens in lenses.items()},
    })
out = {"lenses": {n: {"weights": l.weights, "bands": l.bands,
                      "confidence_floor": l.confidence_floor} for n, l in lenses.items()},
       "cases": cases}
path = ROOT / "web" / "src" / "lib" / "lens.fixtures.json"
path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
print(f"wrote {path.relative_to(ROOT)} ({len(cases)} events)")
