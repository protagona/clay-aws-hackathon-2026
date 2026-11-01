"""The JSON API behind the TypeScript UI (increment 9, A1-A5, B1-B5) through the real FastAPI app."""

import json
import shutil

import pytest
from conftest import FIXTURES, ROOT
from test_backends import add_config
from eval_helpers import eval_config, load

from foundry.engine.evaluate.evaluate import evaluate
from foundry.ui.spa import DIST

BASE = "http://127.0.0.1:8765"
RECEIVER_REFUSED = {"laptop": 400, "cloud": 409}  # a bad form on a laptop; the cloud has no receiver at all (I7)
RUBRIC = {"rubric_id": "clay-adb", "version": "1.0", "task_id": "adb", "description": "d",
          "criteria": [{"id": "accuracy", "name": "Accuracy", "description": "d", "max": 3, "weight": 2,
                        "levels": {"1": "x", "3": "y"}}]}


def _seed(runs, *names):
    runs.mkdir(parents=True, exist_ok=True)
    for n in names:
        shutil.copytree(FIXTURES / n, runs / f"fixture-{n}")


def test_session(backend, tmp_path, monkeypatch):  # A1, U1
    monkeypatch.setenv("AWS_PROFILE", "sandbox")
    backend.sync()
    c = backend.client
    s = c.get("/api/session").json()
    assert s["csrf"] and "offline" in s["configs"] and "adb-001" in s["prompts"] and s["tasks"] == ["adb"]
    assert s["rubric"]["id"] == "adb-rubric-v0" and s["aws_profile"] == "sandbox" and "bedrock" not in s


def test_runs_kpis_and_columns(backend, tmp_path):  # A5, U2
    runs = backend.runs_dir
    _seed(runs, "clean", "recovered", "failed")
    evaluate(runs / "fixture-clean", load(eval_config(tmp_path / "e")))
    rj = json.loads((runs / "fixture-recovered" / "run.json").read_text())
    rj["status"] = "running"
    (runs / "fixture-recovered" / "run.json").write_text(json.dumps(rj))
    (runs / "broken").mkdir()
    (runs / "broken" / "run.json").write_text("{nope")
    backend.sync()
    c = backend.client
    body = c.get("/api/runs").json()
    cols = {r["run_id"]: r["column"] for r in body["runs"]}
    assert cols == {"fixture-clean": "scored", "fixture-recovered": "running", "fixture-failed": "attention",
                    "broken": "attention"}
    k = body["kpis"]
    assert (k["runs"], k["scored"], k["running"], k["live"]) == (4, 1, 1, 1) and abs(k["mean"] - 0.8333) < 0.01
    clean = next(r for r in body["runs"] if r["run_id"] == "fixture-clean")
    assert clean["overall"] == pytest.approx(0.8333, abs=0.01) and clean["workflow"] == 1.0
    failed = next(r for r in body["runs"] if r["run_id"] == "fixture-failed")
    assert failed["reason"] == "Hit the model-call limit (3)" and "reason" not in clean  # board-attention B1


def test_run_detail_and_404(backend, tmp_path):  # A1, A4
    runs = backend.runs_dir
    _seed(runs, "clean", "hostile")
    evaluate(runs / "fixture-clean", load(eval_config(tmp_path / "e")))
    backend.sync()
    c = backend.client
    d = c.get("/api/runs/fixture-clean").json()
    assert d["run"]["run_id"] == "fixture-clean" and d["scores"]["overall"] and "<h2>" in d["report_html"]
    assert d["rubric_info"]["id"] == "adb-rubric-v0" and d["m"]["rows"] and isinstance(d["m"]["feed"][0]["s"], dict)
    assert "report_md" not in d  # the API sends rendered HTML only (increment 16: services return Markdown)
    h = c.get("/api/runs/fixture-hostile").json()
    assert all("<script" not in (f["s"].get("text_html") or "") for f in h["m"]["feed"])
    for bad in ("nope", "a..b", ".ui-jobs", "%2e%2e"):
        r = c.get(f"/api/runs/{bad}")
        assert r.status_code == 404 and r.json() == {"error": "Run not found"}


def test_writes_need_csrf_header_and_json(backend, tmp_path):  # A3
    _seed(backend.runs_dir, "clean")
    backend.sync()
    c, csrf = backend.client, backend.csrf
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


def test_origin_and_host_checked_on_every_write(backend, tmp_path):  # A3
    _seed(backend.runs_dir, "clean")
    backend.sync()
    c, csrf = backend.client, backend.csrf
    h = {"x-csrf-token": csrf, "origin": "http://evil.example"}
    for method, url in (("post", "/api/runs"), ("put", "/api/rubric/drafts/aaaaaaaaaaaa"),
                        ("delete", "/api/rubric/drafts/aaaaaaaaaaaa")):
        r = getattr(c, method)(url, headers=h, **({"json": {}} if method != "delete" else {}))
        assert r.status_code == 403 and r.json() == {"error": "Cross-origin request refused"}
    r = c.get("/api/session", headers={"host": "evil.example:8765"})
    assert r.status_code == 400 and r.json() == {"error": "Bad Host header"}


def test_errors_are_json(backend, tmp_path):  # A4
    backend.sync()
    c, csrf = backend.client, backend.csrf
    r = c.post("/api/runs", json={"prompt_id": "adb-001"}, headers={"x-csrf-token": csrf})
    assert r.status_code == 422 and r.json() == {"error": "config: Field required"}
    r = c.post("/api/runs", json={"prompt_id": "adb-001", "config": "nope"}, headers={"x-csrf-token": csrf})
    assert r.status_code == 400 and "Unknown config 'nope'" in r.json()["error"]
    assert c.get("/api/nope").json() == {"error": "No such API endpoint"}
    assert c.get("/api/batches/b-..%2F..%2Fetc").status_code == 404


def test_start_run_and_evaluate_through_api(backend, tmp_path):  # A1: real subprocesses (fault server: no network)
    add_config(backend, "ok", [{"tool": "echo", "input": {"text": "x"}}, {"text": "done"}])
    c, csrf = backend.client, backend.csrf
    r = c.post("/api/runs", json={"prompt_id": "adb-001", "config": "ok"}, headers={"x-csrf-token": csrf})
    assert r.status_code == 201
    run_id = r.json()["run_id"]
    backend.drain()
    jobs = c.get("/api/jobs").json()["jobs"]
    assert jobs[0]["kind"] == "run" and not jobs[0]["alive"] and jobs[0]["finished_at"] and jobs[0]["run_id"] == run_id
    card = next(x for x in c.get("/api/runs").json()["runs"] if x["run_id"] == run_id)
    assert card["column"] == "attention" and card["headline"] == "not evaluated"
    assert c.post(f"/api/runs/{run_id}/evaluate", headers={"x-csrf-token": csrf}).status_code == 202
    backend.drain()
    card = next(x for x in c.get("/api/runs").json()["runs"] if x["run_id"] == run_id)
    assert card["column"] == "scored" and card["overall"] is not None
    r = c.post("/api/runs/nope/evaluate", headers={"x-csrf-token": csrf})
    assert r.status_code == 404


def test_compare_dashboard_batches_receiver(backend, tmp_path):  # A1
    runs = backend.runs_dir
    _seed(runs, "clean", "recovered")
    evaluate(runs / "fixture-clean", load(eval_config(tmp_path / "e")))
    backend.sync()
    c, csrf = backend.client, backend.csrf
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
    assert r.status_code == RECEIVER_REFUSED[backend.name]


def test_rubric_flow_through_api(backend, tmp_path):  # A1: draft (JSON shortcut), update, activate, rollback, discard
    backend.sync()
    c, csrf = backend.client, backend.csrf
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


def test_spa_served_and_fallback(backend, tmp_path):  # B1, B3
    backend.sync()
    c = backend.client
    for path in ("/", "/runs/whatever", "/rubric/drafts/abc", "/dashboard?rubric=all"):
        r = c.get(path)
        assert r.status_code == 200 and '<div id="root"></div>' in r.text
    for path in ("/classic", "/classic/", "/classic/runs/x"):  # increment 16: the removed UI's paths fall through to the SPA
        r = c.get(path)
        assert r.status_code == 200 and '<div id="root"></div>' in r.text, path
    asset = next((DIST / "assets").glob("*.js")).name
    r = c.get(f"/assets/{asset}")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    index = (DIST / "index.html").read_text()
    for bad in ("..%2Findex.html", "%2e%2e/%2e%2e/spa.py", "%2e%2e/%2e%2e/api.py", "%00", "a%00b"):  # files that exist
        r = c.get(f"/assets/{bad}")
        assert r.status_code == 404 and r.text != index and "def " not in r.text, bad


def test_csp_and_bundle(backend, tmp_path):  # B2, B2a
    backend.sync()
    c = backend.client
    csp = c.get("/").headers["content-security-policy"]
    for part in ("default-src 'none'", "script-src 'self'", "font-src 'self'", "connect-src 'self'",
                 "frame-ancestors 'none'"):
        assert part in csp
    assert "unsafe-eval" not in csp and "script-src 'self' 'unsafe-inline'" not in csp
    index = (DIST / "index.html").read_text()
    assert "<script>" not in index and "<script type=\"module\" crossorigin src=\"/assets/" in index
    for css in (DIST / "assets").glob("*.css"):
        assert "data:font" not in css.read_text() and "url(data:application/font" not in css.read_text()


def test_must_deny_paths(backend, tmp_path, monkeypatch):  # A3: the must-deny cases of the old server-rendered UI, against /api
    runs = backend.runs_dir
    _seed(runs, "clean")
    backend.sync()
    c, csrf = backend.client, backend.csrf
    h = {"x-csrf-token": csrf}
    for bad in ("nope", "a..b", ".ui-jobs", "%2e%2e"):
        assert c.get(f"/api/runs/{bad}").status_code == 404
        assert c.post(f"/api/runs/{bad}/evaluate", headers=h).status_code == 404
    assert c.get("/api/batches/b-..%2F..%2Fetc").status_code == 404
    assert c.get("/api/compare?a=fixture-clean&b=..%2F..%2Fetc").status_code == 404
    assert c.get("/api/rubric?task=nope").status_code == 404
    assert c.post("/api/receiver/start", headers=h, json={"task": "nope"}).status_code == RECEIVER_REFUSED[backend.name]
    import foundry.engine.importer

    monkeypatch.setattr(foundry.engine.importer, "MAX_FILE_BYTES", 1000)
    r = c.post("/api/import", headers=h, data={"task": "adb", "prompt_id": "x"}, files={"file": ("t.json", b"x" * 2000)})
    assert r.status_code == 413
    monkeypatch.setattr(foundry.engine.importer, "MAX_FILE_BYTES", 50 * 1024 * 1024)
    r = c.post("/api/import", headers=h, data={"task": "adb", "prompt_id": "x"}, files={"file": ("t.json", b"nope")})
    assert r.status_code == 400 and ".uploads" not in r.json()["error"] and "t.json" in r.json()["error"]
    for bad in ("..%2Frubric.json", "x.json", "a@b"):
        assert c.get(f"/api/rubric/history/{bad}?task=adb").status_code == 404
        assert c.post("/api/rubric/history/activate", headers=h, json={"task": "adb", "file": bad}).status_code == 404
    for bad in ("ABCDEF123456", "0" * 13):
        assert c.get(f"/api/rubric/drafts/{bad}?task=adb").status_code == 404
        assert c.delete(f"/api/rubric/drafts/{bad}?task=adb", headers=h).status_code == 404
        assert c.get(f"/api/rubric/drafts/{bad}/source?task=adb").status_code == 404


def test_spent_counts_every_run_mean_only_active_rubric(backend, tmp_path):  # A5
    runs = backend.runs_dir
    _seed(runs, "clean", "failed")
    evaluate(runs / "fixture-clean", load(eval_config(tmp_path / "e")))
    backend.sync()
    c = backend.client
    k = c.get("/api/runs").json()["kpis"]
    d_all = c.get("/api/dashboard?rubric=all").json()["d"]["kpis"]
    assert k["spent"] == pytest.approx(d_all["spent"]) and k["scored"] == 1 and k["runs"] == 2


@pytest.mark.laptop_only("symlinked run dirs: the cloud's store holds no symlinks, by construction")
def test_must_deny_paths_continued(backend, tmp_path, monkeypatch):  # A3: the rest of those must-deny cases
    import os
    import socket

    runs = backend.runs_dir
    _seed(runs, "clean", "recovered")
    rj = json.loads((runs / "fixture-recovered" / "run.json").read_text())
    rj["status"] = "running"
    (runs / "fixture-recovered" / "run.json").write_text(json.dumps(rj))
    os.symlink(runs / "fixture-clean", runs / "symdir")  # a symlinked run dir is never followed
    (runs / "symjson").mkdir()
    os.symlink(runs / "fixture-clean" / "run.json", runs / "symjson" / "run.json")
    backend.sync()
    c, csrf = backend.client, backend.csrf
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


@pytest.mark.laptop_only("a symlinked report.md and a rubric lock file: neither exists in the cloud's store")
def test_must_deny_paths_parity(backend, tmp_path):  # A3: the remaining must-deny cases, ported (verify 2)
    import os

    from foundry.data import locks as lock

    runs = backend.runs_dir
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
    root = backend.project
    backend.sync()
    c, csrf = backend.client, backend.csrf
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


def test_second_run_while_one_is_running_is_409(backend, tmp_path):  # A3: one UI-started run at a time
    add_config(backend, "slow", [{"tool": "sleep", "input": {"seconds": 2}}, {"text": "done"}])
    c, h = backend.client, backend.headers()
    busy = c.post("/api/runs", headers=h, json={"prompt_id": "adb-001", "config": "slow"}).json()["run_id"]
    r = c.post("/api/runs", headers=h, json={"prompt_id": "adb-002", "config": "slow"})
    assert r.status_code == 409 and f"already in progress: {busy}" in r.json()["error"]
    backend.drain()
    assert c.post("/api/runs", headers=h, json={"prompt_id": "adb-002", "config": "slow"}).status_code == 201
    backend.drain()


@pytest.mark.laptop_only("a symlinked rubric.json copy: the cloud's store holds no symlinks")
def test_rubric_contents_viewable(backend, tmp_path):  # increment 10: R1, R2, R4
    import os

    runs = backend.runs_dir
    _seed(runs, "clean", "recovered", "failed")
    evaluate(runs / "fixture-clean", load(eval_config(tmp_path / "e")))
    evaluate(runs / "fixture-recovered", load(eval_config(tmp_path / "e2")))
    (runs / "fixture-recovered" / "rubric.json").unlink()  # a symlinked copy is never followed
    os.symlink(ROOT / "tasks/adb/rubric.json", runs / "fixture-recovered" / "rubric.json")
    backend.sync()
    c = backend.client
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


def test_card_kpi_shares_add_up_to_the_totals(backend, tmp_path):  # increment 11, K3: the board totals filtered cards from these
    runs = backend.runs_dir
    _seed(runs, "clean", "failed", "recovered")
    evaluate(runs / "fixture-clean", load(eval_config(tmp_path / "e")))
    trace = runs / "fixture-failed" / "trace.jsonl"  # offline fixtures cost nothing; give one run a real cost
    trace.write_text(trace.read_text().replace('"cost_usd":null', '"cost_usd":0.25', 1))
    (runs / "broken").mkdir()
    (runs / "broken" / "run.json").write_text("{nope")
    backend.sync()
    c = backend.client
    body = c.get("/api/runs").json()
    k, shares = body["kpis"], {r["run_id"]: r["kpi"] for r in body["runs"]}
    assert set(shares) == {"fixture-clean", "fixture-failed", "fixture-recovered", "broken"}
    assert sum(s["spent"] for s in shares.values()) == pytest.approx(k["spent"]) and k["spent"] > 0
    counted = [s["overall"] for s in shares.values() if s["overall"] is not None]
    assert len(counted) == k["scored"] == 1 and sum(counted) / len(counted) == pytest.approx(k["mean"])
    assert shares["fixture-failed"]["spent"] == pytest.approx(0.25) and shares["broken"] == {"spent": 0.0, "overall": None}


def test_compare_rows_say_which_way_is_better(backend, tmp_path):  # increment 12, C6: a cost increase must not read as an improvement
    runs = backend.runs_dir
    _seed(runs, "clean", "recovered")
    for name in ("fixture-clean", "fixture-recovered"):
        evaluate(runs / name, load(eval_config(tmp_path / f"e-{name}")))
    backend.sync()
    c = backend.client
    rows = c.get("/api/compare?a=fixture-clean&b=fixture-recovered").json()["rows"]
    better = {r["label"]: r["better"] for r in rows}
    assert better["Overall"] == better["Workflow"] == better["Output"] == "higher"
    assert better["Cost"] == better["Duration"] == "lower"
    assert better["Model calls"] is None and better["Tool calls (failed)"] is None
    indented = [r for r in rows if r["indent"]]
    assert indented and all(r["better"] == "higher" for r in indented)  # workflow components and output criteria
    assert any(r["label"] == "data-fit" for r in indented)


def test_logo_and_tab_icons_served(backend, tmp_path):  # increment 14, L2/L3: TF mark, charcoal on light and cyan on dark
    backend.sync()
    c = backend.client
    for name in ("logo-light-v1", "logo-dark-v1", "favicon-light-16-v1", "favicon-light-32-v1",
                 "favicon-dark-16-v1", "favicon-dark-32-v1"):
        r = c.get(f"/assets/brand/{name}.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    html = c.get("/").text
    assert 'href="/assets/brand/favicon-light-32-v1.png" media="(prefers-color-scheme: light)"' in html
    assert 'href="/assets/brand/favicon-dark-32-v1.png" media="(prefers-color-scheme: dark)"' in html
    assert "data:image" not in html


@pytest.mark.parametrize("status, steps, reason", [  # board-attention B1, one row of its table each
    ("succeeded", [], None),
    ("running", [], None),
    ("interrupted", [], "Its worker stopped before finishing"),
    ("failed", [{"type": "note", "name": "limit", "text": "max_model_calls (12) reached"}], "Hit the model-call limit (12)"),
    ("failed", [{"type": "note", "name": "limit", "text": "max_input_tokens (200000) reached"}],
     "Hit the input-token limit (200000)"),
    ("timeout", [{"type": "note", "name": "limit", "text": "max_seconds (300) reached"},
                 {"type": "note", "name": "limit", "text": "hard timeout: agent did not stop after cancel"}],
     "Hit the time limit (300 s)"),
    ("timeout", [{"type": "note", "name": "limit", "text": "hard timeout: agent did not stop after cancel"}],
     "Timed out, and the agent didn't stop"),
    ("timeout", [], "Hit the time limit"),
    ("failed", [{"type": "note", "name": "mcp_start", "text": "roda: exited"}], "A tool server didn't start"),
    ("failed", [{"type": "llm_call", "status": "ok"}, {"type": "note", "name": "agent_error", "status": "error",
      "error": "MaxTokensReachedException: Model stopped generating due to maximum token limit."}],
     "Hit the output-token limit; the answer was cut off"),
    ("failed", [{"type": "llm_call", "status": "error", "error": "ThrottlingException: Too many requests, SECRET PROMPT"}],
     "Model call failed: ThrottlingException"),
    ("failed", [{"type": "note", "name": "agent_error", "status": "error", "error": "ValueError: model said SECRET"}],
     "Agent error: ValueError"),
    ("failed", [{"type": "tool_call", "status": "error", "error": "Dataset not found"}], "No final answer"),
])
def test_failure_reason(status, steps, reason):
    from foundry.services.runs import failure_reason

    assert failure_reason(status, steps) == reason


def test_a_failure_reason_never_carries_error_text():
    from foundry.services.runs import failure_reason

    reason = failure_reason("failed", [{"type": "llm_call", "status": "error", "error": "X" * 500 + ": the prompt"}])
    assert reason == "Model call failed: " + "X" * 60
