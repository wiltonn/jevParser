"""The web app end to end, against the real sample workbooks.

Jobs run inline (``run_pending``) instead of on worker threads, and the Jev
client is replaced with one that fails the test if called: with the committed
judgment cache imported, a judged comparison must cost nothing.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from conftest import BOOKS  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = json.loads((ROOT / "tests" / "golden" / "parity.json").read_text())


@pytest.fixture(scope="module")
def app_env(tmp_path_factory):
    import os

    from jev_web import config

    data = tmp_path_factory.mktemp("jevdata")
    saved = {k: os.environ.get(k) for k in ("JEV_DATA_DIR", "JEV_WORKERS", "JEV_WEB_DIST")}
    os.environ.update(JEV_DATA_DIR=str(data), JEV_WORKERS="0",
                      JEV_WEB_DIST=str(data / "no-dist"))
    config.get_settings.cache_clear()
    yield data
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    config.get_settings.cache_clear()


@pytest.fixture(scope="module")
def client(app_env):
    from jev_diff.judge import run as run_mod
    from jev_web.app import create_app

    def no_network(*_a, **_kw):
        raise AssertionError("a judged run asked Jev despite a complete cache")

    original = run_mod.ask_many
    run_mod.ask_many = no_network
    try:
        with TestClient(create_app(start_workers=False)) as c:
            yield c
    finally:
        run_mod.ask_many = original


def drain() -> int:
    from jev_web.jobs.worker import run_pending

    return run_pending()


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


@pytest.fixture(scope="module")
def uploaded(client):
    files = [("files", (p.name, p.read_bytes(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
             for p in BOOKS.values()]
    body = ok(client.post("/api/documents", files=files), 201)
    assert not body["errors"]
    docs = {d["filename"][:4]: d for d in body["documents"]}
    assert set(docs) == {"2026", "2027"}
    return docs


@pytest.fixture(scope="module")
def confirmed(client, uploaded):
    out = {}
    for year, doc in uploaded.items():
        best = doc["detection"]["suggestions"][0]
        out[year] = ok(client.post(f"/api/documents/{doc['id']}/confirm",
                                   json={"model_year_id": best["model_year_id"]}))
    assert drain() == 2
    return out


@pytest.fixture(scope="module")
def cache_loaded(client):
    cache = (ROOT / ".jev-cache" / "judgments.json").read_bytes()
    body = ok(client.post("/api/judgments/import",
                          files={"file": ("judgments.json", cache, "application/json")}))
    assert body["added"] == 398
    return body


@pytest.fixture(scope="module")
def comparison(client, confirmed, cache_loaded):
    y26, y27 = (confirmed[y]["model_year"] for y in ("2026", "2027"))
    created = ok(client.post("/api/comparisons", json={
        "model_id": y26["model_id"], "old_year_id": y26["id"],
        "new_year_id": y27["id"], "judge": True}), 201)
    assert created["status"] == "queued"
    drain()
    return ok(client.get(f"/api/comparisons/{created['id']}"))


# -- catalog and documents -----------------------------------------------------

def test_seed_catalog_and_ruleset(client):
    tree = ok(client.get("/api/catalog"))
    gm = next(o for o in tree if o["prompt_name"] == "GM")
    gmc = next(b for b in gm["brands"] if b["name"] == "GMC")
    yukon = next(m for m in gmc["models"] if m["name"] == "Yukon")
    assert [y["year"] for y in yukon["years"]] == [2026, 2027]
    rulesets = ok(client.get("/api/rulesets"))
    assert rulesets[0]["published_version"] == 1


def test_detection_suggests_the_right_model_year(uploaded):
    for year, doc in uploaded.items():
        best = doc["detection"]["suggestions"][0]
        assert (best["oem"], best["brand"], best["model"], best["year"]) == \
            ("General Motors", "GMC", "Yukon", int(year))
        assert best["model_year_id"] is not None
        assert best["sheet_match"] == 1.0
        assert doc["detection_status"] == "pending"


def test_confirmed_documents_parse_cleanly(client, confirmed):
    for doc in confirmed.values():
        detail = ok(client.get(f"/api/documents/{doc['id']}"))
        assert detail["is_primary"]
        run = detail["parse"]
        assert run["status"] in ("ok", "warnings") and run["fatal"] == 0
        assert len(run["sheets"]) == 14
        assert run["relations"]["requires"] > 0


def test_reupload_is_deduplicated(client, confirmed):
    path = BOOKS["2026"]
    body = ok(client.post("/api/documents", files=[("files", (path.name, path.read_bytes(),
                                                              "application/octet-stream"))]), 201)
    doc = body["documents"][0]
    assert doc["sha256"] == confirmed["2026"]["sha256"]
    assert doc["detection"]["duplicate_of"] == confirmed["2026"]["id"]
    from jev_web.db import SessionLocal
    from jev_web.db.models import Blob

    with SessionLocal() as s:
        assert s.query(Blob).count() == 2
    ok(client.post(f"/api/documents/{doc['id']}/reject"))


def test_rejects_non_order_guide(client):
    body = ok(client.post("/api/documents",
                          files=[("files", ("notes.txt", b"hello", "text/plain"))]), 201)
    assert body["documents"] == []
    assert body["errors"][0]["error"].startswith("notes.txt: not a supported")


def test_ford_pdf_guides_upload_detect_parse_and_compare(client):
    pytest.importorskip("pdfplumber")
    mustang = ROOT / "docs" / "content" / "Ford" / "Mustang"
    files = [("files", (p.name, p.read_bytes(), "application/pdf"))
             for p in sorted(mustang.glob("*-Mustang-Order-Guide.pdf"))]
    body = ok(client.post("/api/documents", files=files), 201)
    assert not body["errors"]
    years = {}
    for doc in body["documents"]:
        assert doc["format"] == "pdf"
        best = doc["detection"]["suggestions"][0]
        assert (best["oem"], best["brand"], best["model"]) == \
            ("Ford Motor Company", "Ford", "Mustang")
        assert best["model_year_id"] is not None and best["sheet_match"] == 1.0
        confirmed = ok(client.post(f"/api/documents/{doc['id']}/confirm",
                                   json={"model_year_id": best["model_year_id"]}))
        years[best["year"]] = confirmed["model_year"]
    assert set(years) == {2025, 2026}
    drain()
    for my in years.values():
        docs = ok(client.get("/api/documents", params={"model_year_id": my["id"]}))
        parse = docs[0]["parse"]
        assert parse["status"] in ("ok", "warnings") and parse["fatal"] == 0
        assert parse["ruleset"] == "Ford order guide (PDF)"

    created = ok(client.post("/api/comparisons", json={
        "model_id": years[2025]["model_id"], "old_year_id": years[2025]["id"],
        "new_year_id": years[2026]["id"], "judge": False}), 201)
    assert created["ruleset"]["name"] == "Ford order guide (PDF)"
    drain()
    c = ok(client.get(f"/api/comparisons/{created['id']}"))
    assert c["status"] == "succeeded", c
    retired = ok(client.get(f"/api/comparisons/{created['id']}/events",
                            params={"kind": "row_removed"}))
    assert {"N4", "B7", "605"} <= {e["code"] for e in retired}
    # An annotated workbook needs an Excel source.
    job = ok(client.post(f"/api/comparisons/{created['id']}/exports",
                         json={"kind": "xlsx"}), 202)
    drain()
    assert "Excel" in ok(client.get(f"/api/jobs/{job['id']}"))["error"]


# -- comparisons ---------------------------------------------------------------

def test_judged_comparison_is_free_and_matches_cli(client, comparison):
    assert comparison["status"] == "succeeded", comparison
    assert comparison["cache"] == {"hits": 316, "misses": 0}
    assert comparison["usage"]["requests"] == 0
    payload = ok(client.get(f"/api/comparisons/{comparison['id']}/payload"))
    assert payload["source"]["old"].startswith("document:")
    payload["source"] = {}
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    assert hashlib.sha256(text.encode()).hexdigest() == GOLDEN["payload_judged"]


def test_event_counts(client, comparison):
    kinds = comparison["summary"]["kinds"]
    assert kinds == {"availability": 8, "constraint": 40, "description": 1, "identity": 4,
                     "membership": 9, "row_added": 5, "row_removed": 15}
    events = ok(client.get(f"/api/comparisons/{comparison['id']}/events",
                           params={"lens": "ordering"}))
    assert len(events) == 82
    values = [e["lens_values"]["ordering"] for e in events]
    assert values == sorted(values, reverse=True)
    rsl = ok(client.get(f"/api/comparisons/{comparison['id']}/events", params={"q": "RSL"}))
    assert any(e["code"] == "RSL" for e in rsl)


def test_html_export_matches_cli_report(client, comparison):
    from jev_diff.render.html import render

    job = ok(client.post(f"/api/comparisons/{comparison['id']}/exports",
                         json={"kind": "html"}), 202)
    drain()
    job = ok(client.get(f"/api/jobs/{job['id']}"))
    assert job["status"] == "succeeded", job
    html = client.get(f"/api/exports/{job['result']['export_id']}/download").text
    payload = ok(client.get(f"/api/comparisons/{comparison['id']}/payload"))
    assert html == render(payload)
    payload["source"] = {}
    assert hashlib.sha256(render(payload).encode()).hexdigest() == GOLDEN["report_html"]


def test_xlsx_export(client, comparison):
    job = ok(client.post(f"/api/comparisons/{comparison['id']}/exports",
                         json={"kind": "xlsx", "lens": "content"}), 202)
    drain()
    job = ok(client.get(f"/api/jobs/{job['id']}"))
    assert job["status"] == "succeeded", job
    data = client.get(f"/api/exports/{job['result']['export_id']}/download").content
    assert data[:2] == b"PK"
    import io

    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data))
    assert wb.sheetnames[0] == "Change Log"


def test_comparison_validation(client, confirmed):
    y26, y27 = (confirmed[y]["model_year"] for y in ("2026", "2027"))
    r = client.post("/api/comparisons", json={"model_id": y26["model_id"],
                                              "old_year_id": y27["id"],
                                              "new_year_id": y26["id"]})
    assert r.status_code == 400 and "before" in r.json()["detail"]


def test_timeline_chains_pairs(client, confirmed, cache_loaded):
    y26, y27 = (confirmed[y]["model_year"] for y in ("2026", "2027"))
    t = ok(client.post("/api/timelines", json={"model_id": y26["model_id"],
                                               "year_ids": [y27["id"], y26["id"]]}), 201)
    assert [(s["old"]["year"], s["new"]["year"]) for s in t["steps"]] == [(2026, 2027)]
    drain()
    t = ok(client.get(f"/api/timelines/{t['id']}"))
    assert t["steps"][0]["status"] == "succeeded"
    history = ok(client.get(f"/api/timelines/{t['id']}/codes/RSL"))
    assert history["steps"][0]["events"]


# -- rulesets --------------------------------------------------------------------

def test_ruleset_draft_edit_test_publish(client, confirmed):
    rs = ok(client.get("/api/rulesets"))[0]
    draft = ok(client.post(f"/api/rulesets/{rs['id']}/draft"))
    assert draft["status"] == "draft" and draft["version"] == 2

    bad = json.loads(json.dumps(draft["body"]))
    bad["clauses"]["anchored"][0]["pattern"] = "^(broken"
    r = client.put(f"/api/ruleset-versions/{draft['id']}", json={"body": bad})
    assert r.status_code == 422 and "regular expression" in json.dumps(r.json()["errors"])

    body = json.loads(json.dumps(draft["body"]))
    for stage in ("anchored", "fallback"):
        body["clauses"][stage] = [p for p in body["clauses"][stage]
                                  if p["relation"] != "requires"]
    saved = ok(client.put(f"/api/ruleset-versions/{draft['id']}",
                          json={"body": body, "notes": "drop requires"}))
    assert saved["content_hash"] != draft["content_hash"]

    diff = ok(client.get(f"/api/ruleset-versions/{draft['id']}/diff"))
    assert diff["against"]["version"] == 1 and diff["changes"]

    job = ok(client.post(f"/api/ruleset-versions/{draft['id']}/test",
                         json={"document_id": confirmed["2027"]["id"],
                               "pair_document_id": confirmed["2026"]["id"]}), 202)
    drain()
    result = ok(client.get(f"/api/jobs/{job['id']}"))["result"]
    assert "requires" not in result["relations"]
    assert result["relations"].get("unstructured", 0) > \
        result["baseline_relations"].get("unstructured", 0)
    assert result["comparison"]["draft"]["events"] and result["comparison"]["baseline"]["events"]

    ok(client.post(f"/api/ruleset-versions/{draft['id']}/publish"))
    r = client.put(f"/api/ruleset-versions/{draft['id']}", json={"body": body})
    assert r.status_code == 400 and "only a draft" in r.json()["detail"]
    # Restore the original rules as v3 so later tests see normal behaviour.
    restore = ok(client.post(f"/api/rulesets/{rs['id']}/draft"))
    v1 = next(v for v in ok(client.get(f"/api/rulesets/{rs['id']}"))["versions"]
              if v["version"] == 1)
    ok(client.put(f"/api/ruleset-versions/{restore['id']}",
                  json={"body": ok(client.get(f"/api/ruleset-versions/{v1['id']}"))["body"]}))
    ok(client.post(f"/api/ruleset-versions/{restore['id']}/publish"))


def test_clause_probe_and_question_preview(client):
    probe = ok(client.post("/api/rules/clause-probe", json={
        "text": "Requires (L84) engine. Not available with (SPZ) Black wheel locks."}))
    assert [p["relation"] for p in probe] == ["requires", "not_available_with"]
    assert probe[0]["codes"] == ["L84"] and probe[0]["ordering"]

    rs = ok(client.get("/api/rulesets"))[0]
    v = ok(client.get(f"/api/ruleset-versions/{rs['versions'][0]['id']}"))
    rendered = ok(client.post("/api/rules/render-questions", json={
        "judge": v["body"]["judge"], "old_year": "2025", "new_year": "2026"}))
    assert rendered["state_keys"]["pair_old"] == "row_2025"
    assert "2025 model year" in rendered["questions"]["alignment"]["instructions"]


def test_resolution_prefers_narrower_scope(client, confirmed):
    y26, y27 = (confirmed[y]["model_year"] for y in ("2026", "2027"))
    base = ok(client.get("/api/rulesets"))[0]
    clone = ok(client.post("/api/rulesets", json={
        "name": "Yukon special", "oem_id": base["oem_id"], "model_id": y26["model_id"],
        "year_from": 2026, "year_to": 2027,
        "clone_from_version_id": base["versions"][0]["id"]}), 201)
    ok(client.post(f"/api/ruleset-versions/{clone['versions'][0]['id']}/publish"))
    found = ok(client.get("/api/rulesets/resolve", params={
        "model_id": y26["model_id"], "year_ids": f"{y26['id']},{y27['id']}"}))
    assert found[0]["ruleset"] == "Yukon special" and found[0]["specificity"] == "model"
    assert found[1]["specificity"] == "oem"
    # Out of range: only the OEM-wide ruleset applies.
    ok(client.patch(f"/api/rulesets/{clone['id']}", json={"year_from": 2030, "year_to": 2031}))
    found = ok(client.get("/api/rulesets/resolve", params={
        "model_id": y26["model_id"], "year_ids": f"{y26['id']},{y27['id']}"}))
    assert [f["specificity"] for f in found] == ["oem"]


# -- jobs, roles, settings -------------------------------------------------------

def test_cancel_queued_job(client, comparison):
    job = ok(client.post(f"/api/comparisons/{comparison['id']}/rerun"))
    cancelled = ok(client.post(f"/api/jobs/{job['id']}/cancel"))
    assert cancelled["status"] == "cancelled"
    assert ok(client.get(f"/api/comparisons/{comparison['id']}"))["status"] == "cancelled"
    assert drain() == 0


def test_stale_running_job_is_recovered(client):
    from jev_web.db import SessionLocal
    from jev_web.db.models import Job, utcnow
    from jev_web.jobs.queue import recover_stale

    with SessionLocal() as s:
        job = Job(type="export", status="running", params={},
                  heartbeat_at=utcnow() - timedelta(minutes=10))
        s.add(job)
        s.commit()
        job_id = job.id
    assert recover_stale() >= 1
    j = ok(client.get(f"/api/jobs/{job_id}"))
    assert j["status"] == "failed" and "interrupted" in j["error"]


def test_analyst_cannot_administer(client):
    ok(client.post("/api/users", json={"email": "ana@localhost", "display_name": "Ana"}), 201)
    as_ana = {"X-Jev-User": "ana@localhost"}
    assert client.get("/api/me", headers=as_ana).json()["role"] == "analyst"
    assert client.get("/api/settings", headers=as_ana).status_code == 403
    assert client.post("/api/rulesets/1/draft", headers=as_ana).status_code == 403
    assert client.get("/api/catalog", headers=as_ana).status_code == 200


def test_secret_settings_are_masked(client, app_env):
    ok(client.put("/api/settings", json={"typesafe.api_key": "sk-test-1234567890"}))
    view = ok(client.get("/api/settings"))
    assert view["typesafe.api_key"] == {"set": True, "hint": "…7890"}
    raw = (app_env / "jev.db").read_bytes()
    assert b"sk-test-1234567890" not in raw
    ok(client.put("/api/settings", json={"typesafe.api_key": None}))
    assert ok(client.get("/api/settings"))["typesafe.api_key"]["set"] is False


def test_writes_are_committed_before_the_response(client):
    """A client that refetches the moment a POST returns must see its write."""
    from jev_web.db import SessionLocal
    from jev_web.db.models import Brand

    from fastapi.routing import APIRoute

    for route in client.app.routes:
        if not isinstance(route, APIRoute):
            continue
        for dep in route.dependant.dependencies:
            for sub in [dep, *dep.dependencies]:
                if getattr(sub.call, "__name__", "") == "get_session":
                    assert sub.scope == "function", route.path

    gm = next(o for o in ok(client.get("/api/catalog")) if o["prompt_name"] == "GM")
    brand = ok(client.post(f"/api/oems/{gm['id']}/brands", json={"name": "Hummer"}), 201)
    with SessionLocal() as s:
        assert s.get(Brand, brand["id"]) is not None
