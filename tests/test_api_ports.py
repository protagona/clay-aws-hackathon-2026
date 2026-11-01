"""Increment 16: the checks only the old server-rendered UI's tests made, ported to /api (dependent-inventory.md,
"Port" rows), so removing that UI loses no coverage. Runs and evaluations are real `foundry` subprocesses, as before."""

import json
import re
import shutil
import time

import pytest
from conftest import FIXTURES, ROOT
from eval_helpers import eval_config, load
from fastapi.testclient import TestClient
from harness_helpers import write_config  # noqa: F401
from test_backends import add_config

from foundry.engine.evaluate.evaluate import evaluate
from foundry.server import create_app

BASE = "http://127.0.0.1:8765"


def _client(runs, root=ROOT):
    app = create_app(runs, root, port=8765)
    c = TestClient(app, base_url=BASE, follow_redirects=False)
    return app, c, {"x-csrf-token": c.get("/api/session").json()["csrf"]}


def _seed(runs, *names):
    runs.mkdir(parents=True, exist_ok=True)
    out = {}
    for n in names:
        shutil.copytree(FIXTURES / n, runs / f"fixture-{n}")
        out[n] = runs / f"fixture-{n}"
    return out


def _project(backend, turns, **kw):
    """The harness's project with config/test.toml driving the fault server (the real tasks/ and offline.toml)."""
    add_config(backend, "test", turns, **kw)
    return backend.project


def _wait(pred, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_ui_binds_localhost_only(monkeypatch):  # was test_binds_localhost_only (AC24)
    import foundry.cli as cli

    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, host, port, **k: seen.update(host=host, port=port))
    assert cli.main(["ui", "--port", "8799"]) == 0
    assert seen == {"host": "127.0.0.1", "port": 8799}


def test_evaluate_holds_and_releases_the_lock(backend, tmp_path):  # was ..._from_ui_holds_and_releases_lock (AC25)
    runs = backend.runs_dir
    _seed(runs, "recovered")
    backend.sync()
    c, h = backend.client, backend.headers()
    assert c.post("/api/runs/fixture-recovered/evaluate", headers=h).status_code == 202
    (job,) = [j for j in c.get("/api/jobs").json()["jobs"] if j["kind"] == "evaluate"]
    second = c.post("/api/runs/fixture-recovered/evaluate", headers=h)
    if job["alive"]:  # the lock is taken before the job starts, so a second click is refused while it runs
        assert second.status_code == 409 and "already being scored" in second.json()["error"]
    backend.drain()
    d = c.get("/api/runs/fixture-recovered").json()
    assert d["evaluating"] is False  # the lock is released whatever the outcome
    (job,) = [j for j in c.get("/api/jobs").json()["jobs"] if j["id"] == job["id"]]
    assert job["exit_code"] == 0, c.get(f"/api/jobs/{job['id']}/log").text
    assert d["scores"]["workflow"]["score"] == pytest.approx(0.93, abs=0.01)


def test_failed_start_is_reported_then_dismissed(backend, tmp_path):  # was test_failed_job_banner
    root = _project(backend, [{"text": "x"}])
    cfg = root / "config" / "test.toml"
    (root / "config" / "broken").mkdir()  # a script that exists (the up-front check passes) but can't load: exit 2
    (root / "config" / "broken" / "adb-001.json").write_text("{}")
    cfg.write_text(re.sub(r'script = ".*"', 'script = "config/broken/{prompt_id}.json"', cfg.read_text()))
    c, h = backend.client, backend.headers()
    assert c.post("/api/runs", headers=h, json={"prompt_id": "adb-001", "config": "test"}).status_code == 201
    backend.drain()
    (j,) = c.get("/api/jobs").json()["jobs"]
    assert j["exit_code"] == 2 and j["failed"] is True and "must be a JSON array of turns" in j["message"]
    assert c.get(f"/api/jobs/{j['id']}/log").status_code == 200
    assert c.post(f"/api/jobs/{j['id']}/dismiss", headers=h).status_code == 200
    assert not any(x["id"] == j["id"] and x["failed"] for x in c.get("/api/jobs").json()["jobs"])


def test_ordinary_failed_run_is_not_a_failed_start(backend, tmp_path):  # was test_ordinary_failed_run_has_no_banner
    _project(backend, [{"text": "x"}], server_args=["--crash-on-start"])
    c, h = backend.client, backend.headers()
    c.post("/api/runs", headers=h, json={"prompt_id": "adb-001", "config": "test"})
    backend.drain()
    (j,) = c.get("/api/jobs").json()["jobs"]
    assert j["exit_code"] == 1 and not j["failed"]  # a run with a card, not a banner
    assert any(r["status"] == "failed" for r in c.get("/api/runs").json()["runs"])


def test_api_never_shows_credentials(backend, tmp_path, monkeypatch):  # was test_ui_pages_never_show_credentials (AC21)
    from test_secrets import SENTINEL, sentinel_env

    for k, v in sentinel_env().items():
        if SENTINEL in v:
            monkeypatch.setenv(k, v)
    _project(backend, [{"tool": "fail", "input": {"message": "x"}}, {"text": "done"}])
    c, h = backend.client, backend.headers()
    run_id = c.post("/api/runs", headers=h, json={"prompt_id": "adb-001", "config": "test"}).json()["run_id"]
    backend.drain()
    (job,) = c.get("/api/jobs").json()["jobs"]
    run_dir = backend.pull(run_id)
    evaluate(run_dir, load(eval_config(tmp_path / "e")))
    backend.sync()
    bodies = [c.get(p).text for p in ("/api/session", "/api/runs", f"/api/runs/{run_id}", "/api/jobs",
                                      f"/api/jobs/{job['id']}/log", "/api/dashboard")]
    assert not any(SENTINEL in b for b in bodies)


def test_security_headers_and_framing(backend, tmp_path):  # was test_security_headers_and_framing (F5), now API and SPA
    backend.sync()
    c = backend.client
    for r in (c.get("/api/session"), c.get("/"), c.get("/api/runs/nope")):
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "same-origin"


def test_render_blocks_protocol_relative_links():  # was test_render_blocks_protocol_relative_links (F6)
    from foundry.api.render import render

    html = render("[a](//evil.example/x) [b](/\\\\evil.example) [c](https://ok.example) [d](runs/x)")
    assert 'href="//evil' not in html and "%5Cevil" not in html
    assert 'href="https://ok.example"' in html and 'href="runs/x"' in html


def test_compare_unevaluated_and_hostile_scores(backend, tmp_path):  # was the server-rendered compare tests (C1)
    runs = backend.runs_dir
    d = _seed(runs, "clean", "recovered", "failed")
    for n in ("clean", "recovered"):
        evaluate(d[n], load(eval_config(tmp_path / f"e-{n}")))
    backend.sync()
    c = backend.client
    body = c.get("/api/compare?a=fixture-clean&b=fixture-failed").json()  # B never evaluated: dashes, not a crash
    assert next(r for r in body["rows"] if r["label"] == "Overall")["b"] == "—" and body["b"]["steps"]
    s = json.loads((d["recovered"] / "scores.json").read_text())
    s["workflow"]["components"] = {"<script>x</script>": "NaN", "ok": True}
    s["output"]["criteria"] = [{"id": "<b>crit</b>", "score": "4"}, "junk", {"id": "m", "score": 3, "max": 4}]
    (d["recovered"] / "scores.json").write_text(json.dumps(s))
    backend.sync()
    r = c.get("/api/compare?a=fixture-clean&b=fixture-recovered")
    assert r.status_code == 200 and r.headers["content-type"] == "application/json"
    assert next(x for x in r.json()["rows"] if x["label"] == "m")["b"] == "3/4"


def test_pipeline_run_detail_compare_and_card(backend, tmp_path):  # was test_pipeline_run_page_compare_and_cards (increment 7)
    from harness_helpers import PROMPT
    from harness_helpers import load as load_cfg

    from foundry.engine.harness.runner import run as run_agent

    turns = [{"text": "1. Search."}, {"tool": "echo", "input": {"text": "hi"}}, {"text": "## Plan\nUse NAIP."},
             {"text": "VERDICT: approve"}]
    cfg = load_cfg(write_config(tmp_path / "cfg", turns, extra='\n[agents]\nmode = "pipeline"\n'))
    runs = backend.runs_dir
    pipe_dir, status = run_agent(cfg, PROMPT, runs)
    assert status == "succeeded"
    _seed(runs, "clean")
    backend.sync()
    c = backend.client
    m = c.get(f"/api/runs/{pipe_dir.name}").json()["m"]
    agents = [r for r in m["rows"] if r["kind"] == "agent"]
    assert [a["label"] for a in agents] == ["planner", "executor", "checker"] and agents[-1]["verdict"] == "approve"
    assert any(f["agent"] == "executor" for f in m["feed"])
    cmp = c.get(f"/api/compare?a={pipe_dir.name}&b=fixture-clean").json()
    assert "scripted-test · pipeline" in cmp["a"]["label"] and "executor" in cmp["a"]["agent_of"].values()
    card = next(r for r in c.get("/api/runs").json()["runs"] if r["run_id"] == pipe_dir.name)
    assert card["agents_mode"] == "pipeline"


def test_run_detail_names_the_rubric_that_scored_it(backend, tmp_path):  # was test_run_page_names_the_rubric_that_scored_it
    runs = backend.runs_dir
    d = _seed(runs, "clean")
    evaluate(d["clean"], load(eval_config(tmp_path / "e")))
    backend.sync()
    c = backend.client
    info = c.get("/api/runs/fixture-clean").json()["rubric_info"]
    assert info["id"] == "adb-rubric-v0" and info["version"] == "0.1"


@pytest.mark.parametrize("bad", [
    {"criteria": [{"id": "a", "name": "A", "description": "d", "max": 3, "weight": 1, "levels": ["w", "m", "r"]}]},
    {"criteria": [{"id": "a", "name": "A", "description": "d", "max": 3, "weight": 1, "levels": "all good"}]},
    {"criteria": 7},
    {"criteria": [{"id": "a", "name": "A", "description": "d", "max": 25, "weight": 1, "levels": {"1": "x", "25": "y"}}]},
    {"criteria": [{"id": "a", "name": "A", "description": "d", "max": 3, "weight": "inf", "levels": {"1": "x", "3": "y"}}]},
])
def test_malformed_model_drafts_open_and_can_be_discarded(backend, tmp_path, monkeypatch, bad):  # was the server-rendered UI's one
    import foundry.engine.models.registry as registry
    from foundry.engine.models.base import Completion

    reply = json.dumps({"rubric_id": "r", "version": "1", "task_id": "adb", "description": "d", **bad})

    class Fake:
        provider, model_id = "fake", "fake"

        def complete(self, req):
            return Completion(reply, 10, 10, "fake")

    monkeypatch.setattr(registry, "build_provider", lambda *a, **k: Fake())
    root = backend.project
    backend.sync()
    c, h = backend.client, backend.headers()
    r = c.post("/api/rubric/drafts", headers=h, data={"task": "adb", "kind": "text", "text": "x", "config": "bedrock"})
    assert r.status_code == 201, r.text
    draft_id = r.json()["draft_id"]
    d = c.get(f"/api/rubric/drafts/{draft_id}?task=adb")
    assert d.status_code == 200 and d.json()["meta"]["valid"] is False
    assert c.delete(f"/api/rubric/drafts/{draft_id}?task=adb", headers=h).status_code == 200
    backend.pull_project()
    drafts = root / "tasks/adb/rubrics/drafts"
    assert not drafts.is_dir() or not list(drafts.iterdir())


def test_batch_validation(backend, tmp_path):  # was test_batch_form_validation
    _project(backend, [{"text": "done"}])
    backend.sync()
    c, h = backend.client, backend.headers()
    base = {"config": "test", "prompts": ["adb-001"], "repeats": 1, "concurrency": 2, "max_cost": 1}
    assert c.post("/api/batches", headers={"x-csrf-token": "nope"}, json=base).status_code == 403
    for bad in ({"config": "../x"}, {"prompts": []}, {"prompts": ["adb-999"]}, {"models": ["not-a-model"]}):
        assert c.post("/api/batches", headers=h, json={**base, **bad}).status_code == 400, bad
    for bad in ({"max_cost": 500}, {"concurrency": 40}):  # the body's own ranges: 422 since increment 17 (Q2)
        assert c.post("/api/batches", headers=h, json={**base, **bad}).status_code == 422, bad
    assert c.get("/api/batches/b-1").status_code == 404


# --- second round (sunset gate pass 1): checks the inventory had wrongly marked "covered" ------------------------

import urllib.request  # noqa: E402


def _hand_otlp():
    def attr(k, v):
        return {"key": k, "value": {"intValue": str(v)} if isinstance(v, int) else {"stringValue": v}}
    spans = [("aa01", "", "invoke_agent ext", 1, {"gen_ai.operation.name": "invoke_agent", "gen_ai.request.model": "ext-model"},
              [{"name": "gen_ai.choice", "attributes": [attr("message", "the plan")]}]),
             ("aa02", "aa01", "chat", 2, {"gen_ai.operation.name": "chat", "gen_ai.usage.input_tokens": 50,
                                          "gen_ai.usage.output_tokens": 5}, []),
             ("aa03", "aa01", "execute_tool search", 3, {"gen_ai.operation.name": "execute_tool",
                                                         "gen_ai.tool.call.arguments": '{"q": "naip"}'}, [])]
    return {"resourceSpans": [{"scopeSpans": [{"spans": [
        {"traceId": "0af7651916cd43dd8448eb211c80319c", "spanId": i, "parentSpanId": p, "name": n,
         "startTimeUnixNano": str(t * 10**9), "endTimeUnixNano": str(t * 10**9 + 10**8), "status": {},
         "attributes": [attr(k, v) for k, v in a.items()], "events": e} for i, p, n, t, a, e in spans]}]}]}


def test_batch_runs_end_to_end(backend, tmp_path):  # was test_batch_from_the_ui
    _project(backend, [{"tool": "echo", "input": {"text": "x"}}, {"text": "done"}])
    c, h = backend.client, backend.headers()
    body = {"config": "test", "prompts": ["adb-001", "adb-002"], "repeats": 1, "concurrency": 2, "evaluate": True, "max_cost": 1}
    r = c.post("/api/batches", headers=h, json=body)
    assert r.status_code == 201, r.text
    batch_id = r.json()["batch_id"]
    assert c.post("/api/batches", headers=h, json=body).status_code in (201, 409)  # one at a time
    backend.drain(timeout=180)
    (job,) = [j for j in c.get("/api/jobs").json()["jobs"] if j["kind"] == "batch" and j["run_id"] == batch_id]
    assert job["exit_code"] == 0, c.get(f"/api/jobs/{job['id']}/log").text[-2000:]
    d = c.get(f"/api/batches/{batch_id}").json()
    assert d["done"] == 2 and all(r["status"] == "succeeded" for r in d["rows"]) and d["variants"]
    links = [r["link"] for r in d["rows"] if r["link"]]
    assert len(links) == 2 and all(name.startswith(f"{batch_id}_") for name in links)
    cards = {r["run_id"]: r for r in c.get("/api/runs").json()["runs"]}
    assert set(links) <= set(cards) and all(cards[x]["column"] == "scored" for x in links)  # ordinary, scored cards
    assert any(b["id"] == batch_id for b in c.get("/api/batches").json()["batches"])


def test_import_a_trace_end_to_end(backend, tmp_path):  # was test_import_upload_from_the_ui
    backend.sync()
    c, h = backend.client, backend.headers()
    files = {"file": ("trace.json", json.dumps(_hand_otlp()), "application/json")}
    r = c.post("/api/import", headers=h, data={"task": "adb", "prompt_id": "ext-1", "config": ""}, files=files)
    assert r.status_code == 201, r.text
    (run_id,) = r.json()["runs"]
    assert run_id.startswith("otel-ext-1-")
    d = c.get(f"/api/runs/{run_id}").json()
    assert d["run"]["model"]["id"] == "ext-model" and any(f["s"]["name"] == "search" for f in d["m"]["feed"])
    bad = {"file": ("x.json", "not json", "application/json")}
    r = c.post("/api/import", headers=h, data={"task": "adb", "prompt_id": "x"}, files=bad)
    assert r.status_code == 400 and "Could not import" in r.json()["error"]
    assert c.post("/api/import", headers=h, data={"task": "nope", "prompt_id": "x"}, files=files).status_code == 400
    assert c.post("/api/import", headers={"x-csrf-token": "bad"}, data={"task": "adb", "prompt_id": "x"}, files=files).status_code == 403


def test_receiver_binds_localhost_and_receives(tmp_path):  # was test_receiver_start_receive_stop_from_the_ui
    app = create_app(tmp_path / "runs", ROOT, port=8765, receiver_port=0)
    c = TestClient(app, base_url=BASE, follow_redirects=False)
    h = {"x-csrf-token": c.get("/api/session").json()["csrf"]}
    assert c.get("/api/receiver").json()["running"] is False
    assert c.post("/api/receiver/start", headers=h, json={"task": "adb", "prompt_id": "live-1", "config": ""}).status_code == 200
    rec = app.state.ui.receiver
    try:
        assert rec.server.server_address[0] == "127.0.0.1"  # security: never on other interfaces
        rec.grace_s = 0.2
        status = c.get("/api/receiver").json()
        assert status["running"] is True and f"127.0.0.1:{rec.port}" in status["endpoint"]
        req = urllib.request.Request(f"http://127.0.0.1:{rec.port}/v1/traces", data=json.dumps(_hand_otlp()).encode(),
                                     headers={"Content-Type": "application/json"})
        assert urllib.request.urlopen(req, timeout=5).status == 200
        assert _wait(lambda: bool(rec.written), 10)
        name = rec.written[0].name
        assert name in c.get("/api/receiver").json()["received"]
        assert any(r["run_id"] == name for r in c.get("/api/runs").json()["runs"])
    finally:
        assert c.post("/api/receiver/stop", headers=h).status_code == 200
    assert app.state.ui.receiver is None and c.get("/api/receiver").json()["running"] is False


@pytest.mark.laptop_only("symlinked run dirs: the cloud's store holds no symlinks, by construction")
def test_symlinked_run_dirs_never_listed(backend, tmp_path):  # was test_symlinked_files_and_dirs_are_never_followed (F1, F2)
    runs = backend.runs_dir
    _seed(runs, "clean")
    outside = tmp_path / "outside"
    shutil.copytree(FIXTURES / "clean", outside / "secretrun")
    rj = json.loads((outside / "secretrun/run.json").read_text())
    rj["prompt_id"] = "OUTSIDE-SECRET"
    (outside / "secretrun/run.json").write_text(json.dumps(rj))
    (runs / "symdir").symlink_to(outside / "secretrun")
    (runs / "symjson").mkdir()
    (runs / "symjson" / "run.json").symlink_to(outside / "secretrun/run.json")
    backend.sync()
    c = backend.client
    body = c.get("/api/runs").text
    assert "OUTSIDE-SECRET" not in body and '"symdir"' not in body
    assert "OUTSIDE-SECRET" not in c.get("/api/dashboard?rubric=all").text


def test_malformed_scores_do_not_break_the_board(backend, tmp_path):  # was test_malformed_files_do_not_break_the_index (F4)
    runs = backend.runs_dir
    d = _seed(runs, "clean", "recovered")
    (d["clean"] / "scores.json").write_text(json.dumps({"overall": "x", "workflow": {"score": "y"}}))
    backend.sync()
    c = backend.client
    r = c.get("/api/runs")
    assert r.status_code == 200
    clean = next(x for x in r.json()["runs"] if x["run_id"] == "fixture-clean")
    assert clean["headline"] == "incomplete"
    assert c.get("/api/runs/fixture-clean").status_code == 200 and c.get("/api/dashboard?rubric=all").status_code == 200


def test_cards_order_and_headlines(backend, tmp_path):  # was test_cards_states_and_order (AC24)
    runs = backend.runs_dir
    d = _seed(runs, "clean", "recovered", "failed")
    evaluate(d["clean"], load(eval_config(tmp_path / "e1")))
    evaluate(d["recovered"], load(eval_config(tmp_path / "e2", judge=[{"text": "x"}, {"text": "y"}])))  # incomplete
    rj = json.loads((d["failed"] / "run.json").read_text())
    rj["status"], rj["started_at"] = "running", "2026-10-29T15:00:00.000Z"
    (d["failed"] / "run.json").write_text(json.dumps(rj))
    backend.sync()
    c = backend.client
    cards = c.get("/api/runs").json()["runs"]
    by = {x["run_id"]: x for x in cards}
    assert by["fixture-recovered"]["headline"] == "incomplete" and by["fixture-failed"]["column"] == "running"
    assert by["fixture-clean"]["overall"] == pytest.approx(0.83, abs=0.01)
    ids = [x["run_id"] for x in cards]
    assert ids.index("fixture-failed") < ids.index("fixture-clean")  # newest (running, 15:00) first


def test_run_text_is_inert_but_still_markdown(backend, tmp_path):  # was test_untrusted_content_rendered_inert + feed markdown
    runs = backend.runs_dir
    d = _seed(runs, "recovered", "hostile")
    trace = d["recovered"] / "trace.jsonl"
    last = max(json.loads(ln)["step"] for ln in trace.read_text().splitlines() if ln.strip())
    text = ("## Plan\n\n| Dataset | Res |\n|---|---|\n| **NAIP** | 0.6 m |\n\n<script>alert(1)</script> "
            "[x](javascript:alert(1)) ![i](https://evil.example/i.png)")
    with open(trace, "a") as fh:
        fh.write(json.dumps({"step": last + 1, "type": "llm_call", "name": "model", "status": "ok",
                             "started_at": "2026-10-29T14:03:00.000Z", "duration_ms": 10, "text": text}) + "\n")
    backend.sync()
    c = backend.client
    feed = c.get("/api/runs/fixture-recovered").json()["m"]["feed"]
    html = next(f["s"]["text_html"] for f in feed if f["s"]["step"] == last + 1)
    assert "<h2>Plan</h2>" in html and "<table>" in html and "<strong>NAIP</strong>" in html  # still Markdown
    assert "<script" not in html and "&lt;script&gt;" in html and 'href="javascript' not in html.lower() and "<img" not in html
    hostile = c.get("/api/runs/fixture-hostile")  # never evaluated: its HTML fields are still inert
    assert hostile.headers["content-type"] == "application/json"
    for f in hostile.json()["m"]["feed"]:
        h_ = f["s"].get("text_html") or ""
        assert "<script" not in h_ and "<img" not in h_ and "javascript:" not in h_.lower()


def test_host_checks_on_every_path(backend, tmp_path):  # was test_host_origin_csrf_checks (AC29), the parts /api lacked
    backend.sync()
    c = backend.client
    for path in ("/", "/runs/x", "/api/session"):
        assert c.get(path, headers={"host": "evil.example"}).status_code == 400
        assert c.get(path, headers={"host": "127.0.0.1:9999"}).status_code == 400  # wrong port
        assert c.get(path, headers={"host": "localhost:8765"}).status_code == 200  # localhost is allowed


def test_start_run_rejects_config_traversal(backend, tmp_path):  # was test_start_run_validation (AC28)
    backend.sync()
    c, h = backend.client, backend.headers()
    r = c.post("/api/runs", headers=h, json={"prompt_id": "adb-001", "config": "../../etc/passwd"})
    assert r.status_code == 400 and "Unknown config" in r.json()["error"]
    r = c.post("/api/runs", headers=h, json={"prompt_id": "adb-999", "config": "offline"})
    assert r.status_code == 400 and "Unknown prompt id 'adb-999' for task 'adb'" in r.json()["error"]


@pytest.mark.parametrize("query", ["", "?a=fixture-clean", "?a=fixture-clean&b=nope", "?a=../x&b=fixture-clean",
                                   "?a=fixture-clean&b=%2e%2e%2fetc"])
def test_compare_bad_ids_404(backend, tmp_path, query):  # was test_compare_bad_ids_404 (C1)
    _seed(tmp_path / "runs", "clean")
    backend.sync()
    c = backend.client
    assert c.get(f"/api/compare{query}").status_code == 404
