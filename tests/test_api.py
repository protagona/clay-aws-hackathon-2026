"""The JSON API behind the TypeScript UI (increment 9, A1-A5, B1-B5) through the real FastAPI app."""

import json
import shutil
import time

import pytest
from conftest import FIXTURES, ROOT
from eval_helpers import eval_config, load
from fastapi.testclient import TestClient

from foundry.evaluate.evaluate import evaluate
from foundry.ui.api import DIST
from foundry.ui.app import create_app

BASE = "http://127.0.0.1:8765"
RUBRIC = {"rubric_id": "clay-adb", "version": "1.0", "task_id": "adb", "description": "d",
          "criteria": [{"id": "accuracy", "name": "Accuracy", "description": "d", "max": 3, "weight": 2,
                        "levels": {"1": "x", "3": "y"}}]}


def _client(tmp_path, root=ROOT):
    app = create_app(tmp_path / "runs", root, port=8765)
    c = TestClient(app, base_url=BASE, follow_redirects=False)
    return app, c, c.get("/api/session").json()["csrf"]


def _seed(runs, *names):
    runs.mkdir(parents=True, exist_ok=True)
    for n in names:
        shutil.copytree(FIXTURES / n, runs / f"fixture-{n}")


def _project(tmp_path):
    root = tmp_path / "proj"
    shutil.copytree(ROOT / "tasks", root / "tasks")
    shutil.copytree(ROOT / "config", root / "config")
    return root


def test_session(tmp_path, monkeypatch):  # A1, U1
    monkeypatch.setenv("AWS_PROFILE", "sandbox")
    _, c, _ = _client(tmp_path)
    s = c.get("/api/session").json()
    assert s["csrf"] and "offline" in s["configs"] and "adb-001" in s["prompts"] and s["tasks"] == ["adb"]
    assert s["rubric"]["id"] == "adb-rubric-v0" and s["aws_profile"] == "sandbox" and "bedrock" not in s


def test_runs_kpis_and_columns(tmp_path):  # A5, U2
    runs = tmp_path / "runs"
    _seed(runs, "clean", "recovered", "failed")
    evaluate(runs / "fixture-clean", load(eval_config(tmp_path / "e")))
    rj = json.loads((runs / "fixture-recovered" / "run.json").read_text())
    rj["status"] = "running"
    (runs / "fixture-recovered" / "run.json").write_text(json.dumps(rj))
    (runs / "broken").mkdir()
    (runs / "broken" / "run.json").write_text("{nope")
    _, c, _ = _client(tmp_path)
    body = c.get("/api/runs").json()
    cols = {r["run_id"]: r["column"] for r in body["runs"]}
    assert cols == {"fixture-clean": "scored", "fixture-recovered": "running", "fixture-failed": "attention",
                    "broken": "attention"}
    k = body["kpis"]
    assert (k["runs"], k["scored"], k["running"], k["live"]) == (4, 1, 1, 1) and abs(k["mean"] - 0.8333) < 0.01
    clean = next(r for r in body["runs"] if r["run_id"] == "fixture-clean")
    assert clean["overall"] == pytest.approx(0.8333, abs=0.01) and clean["workflow"] == 1.0


def test_run_detail_and_404(tmp_path):  # A1, A4
    runs = tmp_path / "runs"
    _seed(runs, "clean", "hostile")
    evaluate(runs / "fixture-clean", load(eval_config(tmp_path / "e")))
    _, c, _ = _client(tmp_path)
    d = c.get("/api/runs/fixture-clean").json()
    assert d["run"]["run_id"] == "fixture-clean" and d["scores"]["overall"] and "<h2>" in d["report_html"]
    assert d["rubric_info"]["id"] == "adb-rubric-v0" and d["m"]["rows"] and isinstance(d["m"]["feed"][0]["s"], dict)
    h = c.get("/api/runs/fixture-hostile").json()
    assert all("<script" not in (f["s"].get("text_html") or "") for f in h["m"]["feed"])
    for bad in ("nope", "a..b", ".ui-jobs", "%2e%2e"):
        r = c.get(f"/api/runs/{bad}")
        assert r.status_code == 404 and r.json() == {"error": "Run not found"}


def test_writes_need_csrf_header_and_json(tmp_path):  # A3
    _seed(tmp_path / "runs", "clean")
    _, c, csrf = _client(tmp_path)
    body = {"prompt_id": "adb-001", "config": "offline"}
    assert c.post("/api/runs", json=body).status_code == 403  # no header
    assert c.post("/api/runs", json=body, headers={"x-csrf-token": "nope"}).status_code == 403
    r = c.post("/api/runs", content=json.dumps(body), headers={"x-csrf-token": csrf, "content-type": "text/plain"})
    assert r.status_code == 415 and "application/json" in r.json()["error"]
    r = c.post("/api/runs/fixture-clean/evaluate", headers={"x-csrf-token": csrf, "content-type": "text/plain"},
               content="x")
    assert r.status_code == 415
    r = c.post("/api/import", headers={"x-csrf-token": csrf}, json={"x": 1})  # upload routes need multipart
    assert r.status_code == 415 and "multipart" in r.json()["error"]


def test_origin_and_host_checked_on_every_write(tmp_path):  # A3
    _seed(tmp_path / "runs", "clean")
    _, c, csrf = _client(tmp_path)
    h = {"x-csrf-token": csrf, "origin": "http://evil.example"}
    for method, url in (("post", "/api/runs"), ("put", "/api/rubric/drafts/aaaaaaaaaaaa"),
                        ("delete", "/api/rubric/drafts/aaaaaaaaaaaa")):
        r = getattr(c, method)(url, headers=h, **({"json": {}} if method != "delete" else {}))
        assert r.status_code == 403 and r.json() == {"error": "Cross-origin request refused"}
    r = c.get("/api/session", headers={"host": "evil.example:8765"})
    assert r.status_code == 400 and r.json() == {"error": "Bad Host header"}


def test_errors_are_json(tmp_path):  # A4
    _, c, csrf = _client(tmp_path)
    r = c.post("/api/runs", json={"prompt_id": "adb-001"}, headers={"x-csrf-token": csrf})
    assert r.status_code == 422 and r.json() == {"error": "config: Field required"}
    r = c.post("/api/runs", json={"prompt_id": "adb-001", "config": "nope"}, headers={"x-csrf-token": csrf})
    assert r.status_code == 400 and "Unknown config 'nope'" in r.json()["error"]
    assert c.get("/api/nope").json() == {"error": "No such API endpoint"}
    assert c.get("/api/batches/b-..%2F..%2Fetc").status_code == 404


def test_start_run_and_evaluate_through_api(tmp_path):  # A1: real subprocesses, offline config
    _, c, csrf = _client(tmp_path)
    r = c.post("/api/runs", json={"prompt_id": "adb-001", "config": "offline"}, headers={"x-csrf-token": csrf})
    assert r.status_code == 201
    run_id = r.json()["run_id"]
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        jobs = c.get("/api/jobs").json()["jobs"]
        if jobs and not jobs[0]["alive"]:
            break
        time.sleep(0.2)
    assert jobs[0]["kind"] == "run" and jobs[0]["finished_at"] and jobs[0]["run_id"] == run_id
    card = next(x for x in c.get("/api/runs").json()["runs"] if x["run_id"] == run_id)
    assert card["column"] == "attention" and card["headline"] == "not evaluated"
    assert c.post(f"/api/runs/{run_id}/evaluate", headers={"x-csrf-token": csrf}).status_code == 202
    while time.monotonic() < deadline:
        card = next(x for x in c.get("/api/runs").json()["runs"] if x["run_id"] == run_id)
        if card["column"] == "scored":
            break
        time.sleep(0.2)
    assert card["column"] == "scored" and card["overall"] is not None
    r = c.post("/api/runs/nope/evaluate", headers={"x-csrf-token": csrf})
    assert r.status_code == 404


def test_compare_dashboard_batches_receiver(tmp_path):  # A1
    runs = tmp_path / "runs"
    _seed(runs, "clean", "recovered")
    evaluate(runs / "fixture-clean", load(eval_config(tmp_path / "e")))
    _, c, csrf = _client(tmp_path)
    cmp = c.get("/api/compare?a=fixture-clean&b=fixture-recovered").json()
    assert cmp["rows"][0]["label"] == "Overall" and cmp["a"]["run"]["run_id"] == "fixture-clean"
    assert c.get("/api/compare?a=fixture-clean&b=nope").status_code == 404
    dash = c.get("/api/dashboard").json()
    assert dash["d"]["kpis"]["scored"] == 1 and dash["total_runs"] == 2
    assert c.get("/api/batches").json() == {"batches": [], "running": False}
    assert c.get("/api/batches/b-1").status_code == 404
    r = c.post("/api/batches", json={"config": "offline", "prompts": []}, headers={"x-csrf-token": csrf})
    assert r.status_code == 400 and "Pick one or more prompts" in r.json()["error"]
    assert c.get("/api/receiver").json()["running"] is False
    r = c.post("/api/receiver/start", json={"task": "../x"}, headers={"x-csrf-token": csrf})
    assert r.status_code == 400


def test_rubric_flow_through_api(tmp_path):  # A1: draft (JSON shortcut), update, activate, rollback, discard
    root = _project(tmp_path)
    _, c, csrf = _client(tmp_path, root=root)
    h = {"x-csrf-token": csrf}
    r = c.post("/api/rubric/drafts", headers=h, data={"task": "adb", "kind": "file", "config": "offline"},
               files={"file": ("clay.json", json.dumps(RUBRIC).encode())})
    assert r.status_code == 201
    did = r.json()["draft_id"]
    d = c.get(f"/api/rubric/drafts/{did}?task=adb").json()
    assert d["meta"]["valid"] and d["body"]["rubric_id"] == "clay-adb"
    bad = {**RUBRIC, "criteria": [{**RUBRIC["criteria"][0], "levels": {"1": "x"}}]}
    r = c.put(f"/api/rubric/drafts/{did}", headers=h, json={"task": "adb", "body": bad})
    assert r.json() == {"valid": False, "error": "criteria.0: levels must describe 3", "field": "criteria.0",
                        "activated": False}
    r = c.put(f"/api/rubric/drafts/{did}", headers=h, json={"task": "adb", "body": bad, "activate": True})
    assert r.status_code == 400 and r.json()["field"] == "criteria.0"
    r = c.put(f"/api/rubric/drafts/{did}", headers=h, json={"task": "adb", "body": RUBRIC, "activate": True})
    assert r.json()["activated"] is True
    ov = c.get("/api/rubric?task=adb").json()
    assert ov["active"]["rubric_id"] == "clay-adb" and len(ov["versions"]) == 2
    r = c.post("/api/rubric/history/activate", headers=h, json={"task": "adb", "file": "adb-rubric-v0@0.1.json"})
    assert r.status_code == 200 and c.get("/api/session").json()["rubric"]["id"] == "adb-rubric-v0"
    assert c.get("/api/rubric/history/clay-adb@1.0.json?task=adb").json()["rubric"]["rubric_id"] == "clay-adb"
    did2 = c.post("/api/rubric/drafts", headers=h, data={"task": "adb", "kind": "file", "config": "offline"},
                  files={"file": ("x.json", json.dumps(RUBRIC).encode())}).json()["draft_id"]
    src = c.get(f"/api/rubric/drafts/{did2}/source?task=adb")
    assert src.headers["content-disposition"].startswith("attachment")
    assert c.delete(f"/api/rubric/drafts/{did2}?task=adb", headers=h).json() == {"ok": True}
    for bad_id in ("../../etc", "ABCDEF123456"):
        assert c.get(f"/api/rubric/drafts/{bad_id}?task=adb").status_code == 404
    assert c.get("/api/rubric?task=../etc").status_code == 404
    r = c.post("/api/rubric/drafts", headers=h, data={"task": "adb", "kind": "text", "text": "x", "config": "offline"})
    assert r.status_code == 502 and "needs a Bedrock judge model" in r.json()["error"]
    r = c.post("/api/rubric/drafts", headers=h, data={"task": "adb", "kind": "file", "config": "offline"},
               files={"file": ("big.pdf", b"x" * 4_600_000)})
    assert r.status_code == 413


def test_spa_served_and_fallback(tmp_path):  # B1, B3
    _, c, _ = _client(tmp_path)
    for path in ("/", "/runs/whatever", "/rubric/drafts/abc", "/dashboard?rubric=all"):
        r = c.get(path)
        assert r.status_code == 200 and '<div id="root"></div>' in r.text
    assert c.get("/classic/").status_code == 200 and "Foundry Eval Sandbox" in c.get("/classic/").text
    assert c.get("/classic").status_code == 307
    asset = next((DIST / "assets").glob("*.js")).name
    r = c.get(f"/assets/{asset}")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    assert c.get("/assets/../api.py").status_code in (404, 200) and "def make_router" not in c.get("/assets/../api.py").text


def test_csp_and_bundle(tmp_path):  # B2, B2a
    _, c, _ = _client(tmp_path)
    csp = c.get("/").headers["content-security-policy"]
    for part in ("default-src 'none'", "script-src 'self'", "font-src 'self'", "connect-src 'self'",
                 "frame-ancestors 'none'"):
        assert part in csp
    assert "unsafe-eval" not in csp and "script-src 'self' 'unsafe-inline'" not in csp
    index = (DIST / "index.html").read_text()
    assert "<script>" not in index and "<script type=\"module\" crossorigin src=\"/assets/" in index
    for css in (DIST / "assets").glob("*.css"):
        assert "data:font" not in css.read_text() and "url(data:application/font" not in css.read_text()


def test_must_deny_paths(tmp_path, monkeypatch):  # A3: the classic UI's must-deny cases, against /api
    runs = tmp_path / "runs"
    _seed(runs, "clean")
    root = _project(tmp_path)
    _, c, csrf = _client(tmp_path, root=root)
    h = {"x-csrf-token": csrf}
    for bad in ("nope", "a..b", ".ui-jobs", "%2e%2e"):
        assert c.get(f"/api/runs/{bad}").status_code == 404
        assert c.post(f"/api/runs/{bad}/evaluate", headers=h).status_code == 404
    assert c.get("/api/batches/b-..%2F..%2Fetc").status_code == 404
    assert c.get("/api/compare?a=fixture-clean&b=..%2F..%2Fetc").status_code == 404
    assert c.get("/api/rubric?task=nope").status_code == 404
    assert c.post("/api/receiver/start", headers=h, json={"task": "nope"}).status_code == 400
    import foundry.importer

    monkeypatch.setattr(foundry.importer, "MAX_FILE_BYTES", 1000)
    r = c.post("/api/import", headers=h, data={"task": "adb", "prompt_id": "x"}, files={"file": ("t.json", b"x" * 2000)})
    assert r.status_code == 413
    monkeypatch.setattr(foundry.importer, "MAX_FILE_BYTES", 50 * 1024 * 1024)
    r = c.post("/api/import", headers=h, data={"task": "adb", "prompt_id": "x"}, files={"file": ("t.json", b"nope")})
    assert r.status_code == 400 and ".uploads" not in r.json()["error"] and "t.json" in r.json()["error"]
    for bad in ("..%2Frubric.json", "x.json", "a@b"):
        assert c.get(f"/api/rubric/history/{bad}?task=adb").status_code == 404
        assert c.post("/api/rubric/history/activate", headers=h, json={"task": "adb", "file": bad}).status_code == 404
    for bad in ("ABCDEF123456", "0" * 13):
        assert c.get(f"/api/rubric/drafts/{bad}?task=adb").status_code == 404
        assert c.delete(f"/api/rubric/drafts/{bad}?task=adb", headers=h).status_code == 404
        assert c.get(f"/api/rubric/drafts/{bad}/source?task=adb").status_code == 404


def test_spent_counts_every_run_mean_only_active_rubric(tmp_path):  # A5
    runs = tmp_path / "runs"
    _seed(runs, "clean", "failed")
    evaluate(runs / "fixture-clean", load(eval_config(tmp_path / "e")))
    _, c, _ = _client(tmp_path)
    k = c.get("/api/runs").json()["kpis"]
    d_all = c.get("/api/dashboard?rubric=all").json()["d"]["kpis"]
    assert k["spent"] == pytest.approx(d_all["spent"]) and k["scored"] == 1 and k["runs"] == 2


def test_must_deny_paths_continued(tmp_path, monkeypatch):  # A3: the rest of the classic must-deny cases
    import os
    import socket

    runs = tmp_path / "runs"
    _seed(runs, "clean", "recovered")
    rj = json.loads((runs / "fixture-recovered" / "run.json").read_text())
    rj["status"] = "running"
    (runs / "fixture-recovered" / "run.json").write_text(json.dumps(rj))
    os.symlink(runs / "fixture-clean", runs / "symdir")  # a symlinked run dir is never followed
    (runs / "symjson").mkdir()
    os.symlink(runs / "fixture-clean" / "run.json", runs / "symjson" / "run.json")
    root = _project(tmp_path)
    _, c, csrf = _client(tmp_path, root=root)
    h = {"x-csrf-token": csrf}
    r = c.post("/api/runs/fixture-recovered/evaluate", headers=h)
    assert r.status_code == 409 and "still running" in r.json()["error"]
    for bad in ("symdir", "symjson"):
        assert c.get(f"/api/runs/{bad}").status_code == 404
    r = c.post("/api/rubric/drafts", headers=h, data={"task": "adb", "kind": "file", "config": "offline"},
               files={"file": ("evil.exe", b"MZ")})
    assert r.status_code == 400 and "Unsupported file type" in r.json()["error"]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, "", ("169.254.169.254", 443))])
    r = c.post("/api/rubric/drafts", headers=h, data={"task": "adb", "kind": "url", "config": "offline",
                                                      "url": "https://metadata.example/latest"})
    assert r.status_code == 400 and "non-public address" in r.json()["error"]


def test_must_deny_paths_classic_parity(tmp_path):  # A3: the remaining classic must-deny cases, ported (verify 2)
    import os

    from foundry.evaluate import lock

    runs = tmp_path / "runs"
    _seed(runs, "clean", "hostile", "recovered")
    evaluate(runs / "fixture-clean", load(eval_config(tmp_path / "e")))
    evaluate(runs / "fixture-hostile", load(eval_config(tmp_path / "e2")))
    (tmp_path / "secret.md").write_text("# TOP SECRET")  # F1: a symlinked report.md is never read
    (runs / "fixture-clean" / "report.md").unlink()
    (runs / "fixture-clean" / "report.md").symlink_to(tmp_path / "secret.md")
    rj = json.loads((runs / "fixture-recovered" / "run.json").read_text())  # F3: ids come from the directory name
    rj["run_id"] = "../../jobs/0000000000000000/dismiss?"
    (runs / "fixture-recovered" / "run.json").write_text(json.dumps(rj))
    (runs / "bad-list").mkdir()  # F4: a run.json that isn't an object
    (runs / "bad-list" / "run.json").write_text("[1, 2]")
    root = _project(tmp_path)
    _, c, csrf = _client(tmp_path, root=root)
    h = {"x-csrf-token": csrf}

    assert "TOP SECRET" not in c.get("/api/runs/fixture-clean").text and "TOP SECRET" not in c.get("/api/runs").text
    ids = [x["run_id"] for x in c.get("/api/runs").json()["runs"]]
    assert "fixture-recovered" in ids and not any("dismiss" in i for i in ids)
    assert c.get("/api/runs/bad-list").status_code == 404
    assert c.post("/api/runs/bad-list/evaluate", headers=h).status_code == 404
    hostile = c.get("/api/runs/fixture-hostile").json()
    html = hostile["report_html"] + "".join(f["s"].get("text_html") or "" for f in hostile["m"]["feed"])
    assert "<script" not in html
    assert "<img" not in html
    assert 'href="javascript' not in html.lower()  # rendered as inert text, never as a link
    assert "<b>bold</b>" not in html

    r = c.post("/api/runs", headers=h, json={"prompt_id": "adb-999", "config": "offline"})
    assert r.status_code == 400 and "Unknown prompt id 'adb-999'" in r.json()["error"]
    token = lock.acquire(runs / "fixture-hostile", owner="test")  # an evaluation already in progress
    try:
        assert c.post("/api/runs/fixture-hostile/evaluate", headers=h).status_code == 409
    finally:
        lock.release(runs / "fixture-hostile", token)

    did = c.post("/api/rubric/drafts", headers=h, data={"task": "adb", "kind": "file", "config": "offline"},
                 files={"file": ("ok.json", json.dumps(RUBRIC).encode())}).json()["draft_id"]
    r = c.put(f"/api/rubric/drafts/{did}", headers=h, json={"task": "adb", "body": {**RUBRIC, "rubric_id": "../escape"},
                                                             "activate": True})
    assert r.status_code == 400 and not list((root / "tasks").rglob("escape*"))
    (root / "tasks/adb/rubrics/.rubric.lock").write_text(str(os.getpid()))  # a live activation in progress
    r = c.put(f"/api/rubric/drafts/{did}", headers=h, json={"task": "adb", "body": RUBRIC, "activate": True})
    assert r.status_code == 409 and "in progress" in r.json()["error"]
    src = c.get(f"/api/rubric/drafts/{did}/source?task=adb")
    assert src.headers["content-disposition"].startswith("attachment") and src.headers["x-content-type-options"] == "nosniff"

    assert c.post("/api/runs", data={"prompt_id": "adb-001", "config": "offline"}).status_code == 403  # a "simple" form post
    assert c.post("/api/jobs/0123456789abcdef/dismiss", headers={"origin": "null", "x-csrf-token": csrf}).status_code == 403
    assert c.get("/api/session", headers={"origin": "http://evil.example"}).headers.get("access-control-allow-origin") is None
    pre = c.options("/api/runs", headers={"origin": "http://evil.example", "access-control-request-method": "POST",
                                          "access-control-request-headers": "x-csrf-token"})
    assert "access-control-allow-origin" not in pre.headers  # no CORS is ever granted


def test_second_run_while_one_is_running_is_409(tmp_path, monkeypatch):  # A3: one UI-started run at a time
    _, c, csrf = _client(tmp_path)
    app_state = c.app.state.ui

    class Alive:
        kind, alive, run_id = "run", True, "busy-run"

    app_state.jobs["0" * 16] = Alive()
    r = c.post("/api/runs", headers={"x-csrf-token": csrf}, json={"prompt_id": "adb-001", "config": "offline"})
    assert r.status_code == 409 and "already in progress: busy-run" in r.json()["error"]


def test_rubric_contents_viewable(tmp_path):  # increment 10: R1, R2, R4
    import os

    runs = tmp_path / "runs"
    _seed(runs, "clean", "recovered", "failed")
    evaluate(runs / "fixture-clean", load(eval_config(tmp_path / "e")))
    evaluate(runs / "fixture-recovered", load(eval_config(tmp_path / "e2")))
    (runs / "fixture-recovered" / "rubric.json").unlink()  # a symlinked copy is never followed
    os.symlink(ROOT / "tasks/adb/rubric.json", runs / "fixture-recovered" / "rubric.json")
    _, c, _ = _client(tmp_path)
    a = c.get("/api/rubric/active?task=adb").json()
    assert a["rubric"]["rubric_id"] == "adb-rubric-v0" and len(a["rubric"]["criteria"]) == 4 and a["active"]
    assert len(a["sha256"]) == 64 and a["file"] is None  # not in a history yet
    r = c.get("/api/runs/fixture-clean/rubric").json()
    assert r["rubric"]["criteria"][0]["levels"]["1"] and r["sha256"] == a["sha256"] and r["active"]
    for bad in ("fixture-failed", "fixture-recovered", "nope", "a..b"):
        assert c.get(f"/api/runs/{bad}/rubric").status_code == 404
    (runs / "fixture-clean" / "rubric.json").write_text("{not a rubric")
    assert c.get("/api/runs/fixture-clean/rubric").status_code == 404
    assert c.get("/api/rubric/active?task=nope").status_code == 404
    assert c.get("/api/dashboard?rubric=all").json()["scored_by"][a["sha256"]] in ("fixture-clean", "fixture-recovered")
