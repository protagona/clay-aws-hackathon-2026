"""UI (AC24-AC29) through the real FastAPI app; runs and evaluations are real `foundry` subprocesses."""

import json
import re
import urllib.request
import shutil
import time

import pytest
from conftest import FIXTURES, ROOT
from eval_helpers import eval_config, load
from fastapi.testclient import TestClient
from harness_helpers import write_config

from foundry.evaluate import lock
from foundry.evaluate.evaluate import evaluate
from foundry.ui.app import create_app

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


def _client(runs, root=ROOT):
    app = create_app(runs, root, port=PORT)
    return app, TestClient(app, base_url=BASE, follow_redirects=False)


def _csrf(app):
    return app.state.ui.csrf


def _seed(runs, *names):
    runs.mkdir(parents=True, exist_ok=True)
    out = {}
    for n in names:
        dest = runs / f"fixture-{n}"
        shutil.copytree(FIXTURES / n, dest)
        out[n] = dest
    return out


def _project(tmp_path, turns, **kw):
    """A throwaway project root: config/test.toml driving the fault server, plus the real tasks/."""
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    shutil.copytree(ROOT / "tasks", root / "tasks")
    cfg = write_config(tmp_path / "cfgsrc", turns, **kw)
    shutil.copy(cfg, root / "config" / "test.toml")
    shutil.copy(ROOT / "config" / "offline.toml", root / "config" / "offline.toml")
    return root


def _wait(pred, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_binds_localhost_only(monkeypatch):  # AC24
    import foundry.cli as cli

    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, host, port, **k: seen.update(host=host, port=port))
    assert cli.main(["ui", "--port", "8799"]) == 0
    assert seen == {"host": "127.0.0.1", "port": 8799}


def test_empty_state(tmp_path):  # AC24
    _, c = _client(tmp_path / "runs")
    r = c.get("/")
    assert r.status_code == 200
    assert 'No runs yet. Start one above or run <code>foundry run --config config/offline.toml --prompt adb-001</code>.' in r.text


def test_cards_states_and_order(tmp_path):  # AC24
    runs = tmp_path / "runs"
    d = _seed(runs, "clean", "recovered", "failed", "injection")
    evaluate(d["clean"], load(eval_config(tmp_path / "e1")))
    evaluate(d["recovered"], load(eval_config(tmp_path / "e2", judge=[{"text": "x"}, {"text": "y"}])))  # incomplete
    rj = json.loads((d["failed"] / "run.json").read_text())
    rj["status"], rj["started_at"] = "running", "2026-10-29T15:00:00.000Z"
    (d["failed"] / "run.json").write_text(json.dumps(rj))
    (runs / "broken").mkdir()
    (runs / "broken" / "run.json").write_text("{not json")
    _, c = _client(runs)
    html = c.get("/").text
    assert "0.83" in html and "W1.00 O.67" in html                 # evaluated: overall + W/O
    assert "incomplete" in html and "not evaluated" in html and "running" in html
    assert "unreadable run.json" in html and "broken" in html
    assert "adb · …-clean" not in html and "adb · …lean" in html     # footer: task · last 4 of run id
    assert html.index("fixture-failed") < html.index("fixture-clean")  # newest (running, 15:00) first


def test_report_page_and_unevaluated_page(tmp_path):  # AC25
    runs = tmp_path / "runs"
    d = _seed(runs, "clean", "recovered")
    evaluate(d["clean"], load(eval_config(tmp_path / "e")))
    _, c = _client(runs)
    r = c.get("/runs/fixture-clean")
    assert r.status_code == 200 and "<h2>Workflow score: 1.00</h2>" in r.text
    assert re.search(r'class="ring" style="--p:83\.3">\s*<span class="ring-num">0\.83</span><span class="ring-label">Overall', r.text)
    assert "All steps (" in r.text  # the feed folds under the report once evaluated
    r = c.get("/runs/fixture-recovered")
    assert 'action="/runs/fixture-recovered/evaluate"' in r.text and 'class="waterfall"' in r.text
    assert "retry of 6" in r.text


# ("/runs/.." is normalized to "/" by the HTTP client before it reaches the app, so it lands on the index.)
@pytest.mark.parametrize("path", ["/runs/a..b", "/runs/nope", "/runs/%2e%2e", "/runs/fixture-clean%2F..",
                                  "/runs/.ui-jobs"])
def test_bad_run_ids_404(tmp_path, path):  # AC26
    runs = tmp_path / "runs"
    _seed(runs, "clean")
    _, c = _client(runs)
    r = c.get(path)
    assert r.status_code == 404


def test_untrusted_content_rendered_inert(tmp_path):  # AC27
    runs = tmp_path / "runs"
    d = _seed(runs, "hostile")
    evaluate(d["hostile"], load(eval_config(tmp_path / "e")))
    _, c = _client(runs)
    for path in ("/runs/fixture-hostile",):
        html = c.get(path).text
        assert "<script>" not in html
        assert 'href="javascript' not in html.lower()
        assert "<img" not in html
        assert "&lt;script&gt;" in html
    rj = json.loads((d["hostile"] / "run.json").read_text())  # also the un-evaluated page (raw steps)
    shutil.copytree(d["hostile"], runs / "h2")
    for f in ("scores.json", "report.md", "rubric.json"):
        (runs / "h2" / f).unlink()
    rj["run_id"] = "h2"
    (runs / "h2" / "run.json").write_text(json.dumps(rj))
    html = c.get("/runs/h2").text
    assert "<b>bold</b>" not in html and "&lt;b&gt;bold&lt;/b&gt;" in html


def test_host_origin_csrf_checks(tmp_path):  # AC29
    app, c = _client(tmp_path / "runs")
    assert c.get("/", headers={"host": "evil.example"}).status_code == 400
    assert c.get("/", headers={"host": "127.0.0.1:9999"}).status_code == 400
    assert c.get("/", headers={"host": f"localhost:{PORT}"}).status_code == 200
    form = {"prompt_id": "adb-001", "config": "offline", "csrf": _csrf(app)}
    assert c.post("/runs", data=form, headers={"host": "evil.example"}).status_code == 400
    assert c.post("/runs", data=form, headers={"origin": "http://evil.example"}).status_code == 403
    assert c.post("/runs", data={**form, "csrf": "wrong"}).status_code == 403
    assert c.post("/runs", data={k: v for k, v in form.items() if k != "csrf"}).status_code == 403
    assert c.post("/runs/fixture-x/evaluate", data={"csrf": "wrong"}).status_code == 403
    assert c.post("/jobs/0123456789abcdef/dismiss", data={"csrf": "wrong"}).status_code == 403


def test_start_run_validation(tmp_path):  # AC28
    app, c = _client(tmp_path / "runs")
    r = c.post("/runs", data={"prompt_id": "adb-001", "config": "../../etc/passwd", "csrf": _csrf(app)})
    assert r.status_code == 400 and "Unknown config" in r.text
    r = c.post("/runs", data={"prompt_id": "adb-999", "config": "offline", "csrf": _csrf(app)})
    assert r.status_code == 400 and "Unknown prompt id &#39;adb-999&#39; for task &#39;adb&#39;" in r.text


def test_start_run_then_409_then_card(tmp_path):  # AC28, AC29
    root = _project(tmp_path, [{"tool": "sleep", "input": {"seconds": 2}}, {"text": "done"}])
    runs = tmp_path / "runs"
    app, c = _client(runs, root)
    form = {"prompt_id": "adb-001", "config": "test", "csrf": _csrf(app)}
    r = c.post("/runs", data=form)
    assert r.status_code == 303 and r.headers["location"] == "/"
    (job,) = app.state.ui.jobs.values()
    r2 = c.post("/runs", data=form)
    assert r2.status_code == 409 and f"A run is already in progress: {job.run_id}" in r2.text
    assert _wait(lambda: job.exit_code is not None)
    assert job.exit_code == 0, job.log.read_text()
    html = c.get("/").text
    assert "adb-001" in html and "not evaluated" in html and "succeeded" in html


def test_evaluate_from_ui_holds_and_releases_lock(tmp_path):  # AC25, AC29
    runs = tmp_path / "runs"
    d = _seed(runs, "recovered")
    app, c = _client(runs)
    r = c.post("/runs/fixture-recovered/evaluate", data={"csrf": _csrf(app)})
    assert r.status_code == 303 and r.headers["location"] == "/runs/fixture-recovered"
    (job,) = app.state.ui.jobs.values()
    second = c.post("/runs/fixture-recovered/evaluate", data={"csrf": _csrf(app)})
    if job.exit_code is None:  # the lock is taken before the subprocess starts, so a second click is refused
        assert second.status_code == 409 and "already being evaluated" in second.text
    assert _wait(lambda: job.exit_code is not None and not lock.is_locked(d["recovered"]))
    assert job.exit_code == 0, job.log.read_text()
    assert (d["recovered"] / "scores.json").exists()
    assert "<h2>Workflow score: 0.93</h2>" in c.get("/runs/fixture-recovered").text


def test_evaluate_refused_for_running_run(tmp_path):
    runs = tmp_path / "runs"
    d = _seed(runs, "clean")
    rj = json.loads((d["clean"] / "run.json").read_text())
    rj["status"] = "running"
    (d["clean"] / "run.json").write_text(json.dumps(rj))
    app, c = _client(runs)
    page = c.get("/runs/fixture-clean").text
    assert "If this run crashed, evaluate it from the CLI with --force." in page and "disabled" in page
    r = c.post("/runs/fixture-clean/evaluate", data={"csrf": _csrf(app)})
    assert r.status_code == 409


def test_failed_job_banner(tmp_path):
    root = _project(tmp_path, [{"text": "x"}])
    cfg = root / "config" / "test.toml"
    cfg.write_text(re.sub(r'script = ".*"', 'script = "/nonexistent/{prompt_id}.json"', cfg.read_text()))
    runs = tmp_path / "runs"
    app, c = _client(runs, root)
    assert c.post("/runs", data={"prompt_id": "adb-001", "config": "test", "csrf": _csrf(app)}).status_code == 303
    (job,) = app.state.ui.jobs.values()
    assert _wait(lambda: job.exit_code is not None) and job.exit_code == 2
    html = c.get("/").text
    assert "Run adb-001 failed to start:" in html and "Scripted model script not found" in html
    assert c.get(f"/jobs/{job.id}/log").status_code == 200
    assert c.post(f"/jobs/{job.id}/dismiss", data={"csrf": _csrf(app)}).status_code == 303
    assert "failed to start" not in c.get("/").text


def test_ordinary_failed_run_has_no_banner(tmp_path):
    root = _project(tmp_path, [{"text": "x"}], server_args=["--crash-on-start"])
    runs = tmp_path / "runs"
    app, c = _client(runs, root)
    c.post("/runs", data={"prompt_id": "adb-001", "config": "test", "csrf": _csrf(app)})
    (job,) = app.state.ui.jobs.values()
    assert _wait(lambda: job.exit_code is not None) and job.exit_code == 1
    html = c.get("/").text
    assert "failed to start:" not in html and "failed" in html


def test_ui_pages_never_show_credentials(tmp_path, monkeypatch):  # AC21 (UI part)
    from test_secrets import SENTINEL, sentinel_env

    for k, v in sentinel_env().items():
        if SENTINEL in v:
            monkeypatch.setenv(k, v)
    root = _project(tmp_path, [{"tool": "fail", "input": {"message": "x"}}, {"text": "done"}])
    runs = tmp_path / "runs"
    app, c = _client(runs, root)
    c.post("/runs", data={"prompt_id": "adb-001", "config": "test", "csrf": _csrf(app)})
    (job,) = app.state.ui.jobs.values()
    assert _wait(lambda: job.exit_code is not None)
    (run_dir,) = [p for p in runs.iterdir() if p.is_dir() and not p.name.startswith(".")]
    evaluate(run_dir, load(eval_config(tmp_path / "e")))
    pages = [c.get("/").text, c.get(f"/runs/{run_dir.name}").text, c.get(f"/jobs/{job.id}/log").text]
    assert not any(SENTINEL in p for p in pages)


# --- regression tests for the Verify-stage pentest findings (F1-F6) ---------------------------------------

def test_symlinked_files_and_dirs_are_never_followed(tmp_path):  # F1, F2
    runs = tmp_path / "runs"
    d = _seed(runs, "clean")
    evaluate(d["clean"], load(eval_config(tmp_path / "e")))
    outside = tmp_path / "outside"
    shutil.copytree(FIXTURES / "clean", outside / "secretrun")
    (outside / "secret.md").write_text("# TOP SECRET OUTSIDE FILE")
    rj = json.loads((outside / "secretrun/run.json").read_text())
    rj["prompt_id"] = "OUTSIDE-SECRET"
    (outside / "secretrun/run.json").write_text(json.dumps(rj))

    (d["clean"] / "report.md").unlink()
    (d["clean"] / "report.md").symlink_to(outside / "secret.md")
    (runs / "symdir").symlink_to(outside / "secretrun")
    (runs / "symjson").mkdir()
    (runs / "symjson" / "run.json").symlink_to(outside / "secretrun/run.json")
    _, c = _client(runs)
    assert "TOP SECRET" not in c.get("/runs/fixture-clean").text
    assert c.get("/runs/symdir").status_code == 404
    assert c.get("/runs/symjson").status_code == 404
    index = c.get("/").text
    assert "OUTSIDE-SECRET" not in index and 'href="/runs/symdir"' not in index


def test_links_use_directory_name_not_run_json_run_id(tmp_path):  # F3
    runs = tmp_path / "runs"
    d = _seed(runs, "clean")
    rj = json.loads((d["clean"] / "run.json").read_text())
    rj["run_id"] = "../../jobs/0000000000000000/dismiss?"
    (d["clean"] / "run.json").write_text(json.dumps(rj))
    _, c = _client(runs)
    assert 'href="/runs/fixture-clean"' in c.get("/").text
    page = c.get("/runs/fixture-clean").text
    assert 'action="/runs/fixture-clean/evaluate"' in page and "dismiss" not in page


def test_malformed_files_do_not_break_the_index(tmp_path):  # F4
    runs = tmp_path / "runs"
    d = _seed(runs, "clean", "recovered")
    (d["clean"] / "scores.json").write_text(json.dumps({"overall": "x", "workflow": {"score": "y"}}))
    (runs / "bad-list").mkdir()
    (runs / "bad-list" / "run.json").write_text("[1, 2]")
    app, c = _client(runs)
    r = c.get("/")
    assert r.status_code == 200 and "incomplete" in r.text and "unreadable run.json" in r.text
    assert c.get("/runs/bad-list").status_code == 404
    assert c.post("/runs/bad-list/evaluate", data={"csrf": _csrf(app)}).status_code == 404


def test_security_headers_and_framing(tmp_path):  # F5
    _, c = _client(tmp_path / "runs")
    for r in (c.get("/"), c.get("/runs/nope")):
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"


def test_origin_checked_on_evaluate_post(tmp_path):  # AC29
    runs = tmp_path / "runs"
    _seed(runs, "clean")
    app, c = _client(runs)
    r = c.post("/runs/fixture-clean/evaluate", data={"csrf": _csrf(app)}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_render_blocks_protocol_relative_links():  # F6
    from foundry.ui.render import render

    html = render("[a](//evil.example/x) [b](/\\\\evil.example) [c](https://ok.example) [d](runs/x)")
    assert 'href="//evil' not in html and "%5Cevil" not in html
    assert 'href="https://ok.example"' in html and 'href="runs/x"' in html


def test_auto_refresh_only_while_in_progress(tmp_path):  # increment 2, R4
    runs = tmp_path / "runs"
    d = _seed(runs, "clean", "recovered")
    _, c = _client(runs)
    refresh = '<meta http-equiv="refresh" content="3">'
    assert refresh not in c.get("/").text and refresh not in c.get("/runs/fixture-clean").text
    rj = json.loads((d["clean"] / "run.json").read_text())
    rj["status"] = "running"
    (d["clean"] / "run.json").write_text(json.dumps(rj))
    assert refresh in c.get("/").text and refresh in c.get("/runs/fixture-clean").text
    assert refresh not in c.get("/runs/fixture-recovered").text
    token = lock.acquire(d["recovered"], owner="ui")  # an evaluation in progress
    try:
        assert refresh in c.get("/runs/fixture-recovered").text
    finally:
        lock.release(d["recovered"], token)


# --- batches, import and receiver from the UI ----------------------------------------------------------------

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


def test_nav_on_every_page(tmp_path):
    _, c = _client(tmp_path / "runs")
    for path in ("/", "/batches", "/import"):
        html = c.get(path).text
        assert 'href="/batches"' in html and 'href="/import"' in html and 'aria-current="page"' in html


def test_batch_from_the_ui(tmp_path):
    root = _project(tmp_path, [{"tool": "echo", "input": {"text": "x"}}, {"text": "done"}])
    runs = tmp_path / "runs"
    app, c = _client(runs, root)
    form = {"config": "test", "prompts": ["adb-001", "adb-002"], "repeats": "1", "concurrency": "2",
            "evaluate": "1", "max_cost": "1", "csrf": _csrf(app)}
    r = c.post("/batches", data=form)
    assert r.status_code == 303 and r.headers["location"].startswith("/batches/b-")
    batch_id = r.headers["location"].rsplit("/", 1)[1]
    assert c.post("/batches", data=form).status_code in (303, 409)  # one batch at a time (409 while running)
    (job,) = [j for j in app.state.ui.jobs.values() if j.kind == "batch"]
    assert _wait(lambda: job.exit_code is not None, 120), "batch did not finish"
    assert job.exit_code == 0, job.log.read_text()[-2000:]
    page = c.get(f"/batches/{batch_id}").text
    assert "2/2 jobs" in page and page.count('>report</a>') == 2 and "succeeded" in page
    assert re.search(r"<th>Mean</th><th>Min</th><th>Max</th><th><abbr[^>]*>Stdev</abbr></th>", page)  # D-7
    links = re.findall(r'href="/runs/([^"]+)"', page)
    assert links and all(name.startswith(f"{batch_id}_") for name in links)
    assert c.get(f"/runs/{links[0]}").status_code == 200
    index = c.get("/").text
    assert all(f"/runs/{name}" in index for name in links)  # batch runs are ordinary cards too
    assert batch_id in c.get("/batches").text


def test_batch_form_validation(tmp_path):
    root = _project(tmp_path, [{"text": "done"}])
    app, c = _client(tmp_path / "runs", root)
    base = {"config": "test", "prompts": ["adb-001"], "repeats": "1", "concurrency": "2", "max_cost": "1", "csrf": _csrf(app)}
    assert c.post("/batches", data={**base, "csrf": "nope"}).status_code == 403
    assert c.post("/batches", data={**base, "config": "../x"}).status_code == 400
    assert c.post("/batches", data={**base, "prompts": []}).status_code == 400
    assert c.post("/batches", data={**base, "prompts": ["adb-999"]}).status_code == 400
    assert c.post("/batches", data={**base, "models": ["not-a-model"]}).status_code == 400
    assert c.post("/batches", data={**base, "max_cost": "500"}).status_code == 400
    assert c.post("/batches", data={**base, "concurrency": "40"}).status_code == 400
    assert c.get("/batches/../../etc").status_code == 404 and c.get("/batches/b-1").status_code == 404


def test_import_upload_from_the_ui(tmp_path):
    runs = tmp_path / "runs"
    app, c = _client(runs)
    files = {"file": ("trace.json", json.dumps(_hand_otlp()), "application/json")}
    r = c.post("/import", data={"task": "adb", "prompt_id": "ext-1", "config": "", "csrf": _csrf(app)}, files=files)
    assert r.status_code == 303 and r.headers["location"].startswith("/runs/otel-ext-1-")
    page = c.get(r.headers["location"]).text
    assert "ext-model" in page and "search" in page
    bad = {"file": ("x.json", "not json", "application/json")}
    r = c.post("/import", data={"task": "adb", "prompt_id": "x", "csrf": _csrf(app)}, files=bad)
    assert r.status_code == 400 and "Could not import" in r.text
    r = c.post("/import", data={"task": "nope", "prompt_id": "x", "csrf": _csrf(app)}, files=files)
    assert r.status_code == 400
    assert c.post("/import", data={"task": "adb", "prompt_id": "x", "csrf": "bad"}, files=files).status_code == 403


def test_receiver_start_receive_stop_from_the_ui(tmp_path):
    runs = tmp_path / "runs"
    app = create_app(runs, ROOT, port=PORT, receiver_port=0)
    c = TestClient(app, base_url=BASE, follow_redirects=False)
    assert "Start receiving" in c.get("/import").text
    assert c.post("/receiver/start", data={"task": "adb", "prompt_id": "live-1", "csrf": _csrf(app)}).status_code == 303
    rec = app.state.ui.receiver
    try:
        assert rec.server.server_address[0] == "127.0.0.1"
        rec.grace_s = 0.2
        page = c.get("/import").text
        assert "Receiving" in page and f"127.0.0.1:{rec.port}" in page and 'http-equiv="refresh"' in page
        req = urllib.request.Request(f"http://127.0.0.1:{rec.port}/v1/traces", data=json.dumps(_hand_otlp()).encode(),
                                     headers={"Content-Type": "application/json"})
        assert urllib.request.urlopen(req, timeout=5).status == 200
        assert _wait(lambda: bool(rec.written), 10)
        assert rec.written[0].name in c.get("/import").text and rec.written[0].name in c.get("/").text
    finally:
        assert c.post("/receiver/stop", data={"csrf": _csrf(app)}).status_code == 303
    assert app.state.ui.receiver is None and "Start receiving" in c.get("/import").text


def test_browser_form_post_is_not_refused_as_cross_origin(tmp_path):
    """Regression: Referrer-Policy no-referrer made browsers send `Origin: null`, so every form POST got 403."""
    root = _project(tmp_path, [{"text": "done"}])
    app, c = _client(tmp_path / "runs", root)
    assert c.get("/").headers["referrer-policy"] == "same-origin"
    # what a browser sends for a same-origin form under that policy
    r = c.post("/runs", data={"prompt_id": "adb-001", "config": "test", "csrf": _csrf(app)},
               headers={"origin": BASE})
    assert r.status_code == 303
    assert c.post("/runs", data={"prompt_id": "adb-001", "config": "test", "csrf": _csrf(app)},
                  headers={"origin": "null"}).status_code == 403  # still refused if a page strips its origin


# --- compare two runs (increment-4 C1, C2) ---

def _evaluated(tmp_path, *names):
    from foundry.evaluate.evaluate import evaluate

    d = _seed(tmp_path / "runs", *names)
    cfg = load(eval_config(tmp_path / "cfg"))
    for n in names:
        evaluate(d[n], cfg)
    return d


def test_compare_page_rows_and_timelines(tmp_path):  # C1
    _evaluated(tmp_path, "clean", "recovered")
    _, c = _client(tmp_path / "runs")
    page = c.get("/compare?a=fixture-clean&b=fixture-recovered").text
    rows = {m[0]: m[1:] for m in re.findall(
        r"<tr><td[^>]*>([^<]+)</td>\s*<td>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td></tr>", page)}
    assert rows["Overall"][:2] == ("0.83", "0.80") and rows["Overall"][2] == "−0.04"
    assert rows["Workflow"] == ("1.00", "0.93", "−0.07") and rows["reliability"] == ("1.00", "0.50", "−0.50")
    assert rows["sequence"] == ("1.00", "1.00", "0")
    assert rows["Tool calls (errors)"][:2] == ("2 (0)", "4 (2)") and rows["Tool calls (errors)"][2] == "+2"
    assert any(k in rows for k in ("data-fit", "method"))  # one row per rubric criterion, as n/max
    assert re.search(r"<td>\d+/\d+</td>", page)
    assert page.index("A timeline") < page.index("B timeline")
    assert 'href="/runs/fixture-clean"' in page and 'href="/runs/fixture-recovered"' in page


def test_compare_unevaluated_run_shows_dashes(tmp_path):  # C1
    _seed(tmp_path / "runs", "clean", "failed")
    _, c = _client(tmp_path / "runs")
    page = c.get("/compare?a=fixture-clean&b=fixture-failed").text
    assert "not evaluated yet" in page and "<td>—</td>" in page and "B timeline" in page


@pytest.mark.parametrize("query", ["", "?a=fixture-clean", "?a=fixture-clean&b=nope", "?a=../x&b=fixture-clean",
                                   "?a=fixture-clean&b=%2e%2e%2fetc"])
def test_compare_bad_ids_404(tmp_path, query):  # C1
    _seed(tmp_path / "runs", "clean")
    _, c = _client(tmp_path / "runs")
    assert c.get(f"/compare{query}").status_code == 404


def test_run_page_compare_form(tmp_path):  # C2
    _seed(tmp_path / "runs", "clean", "recovered", "failed")
    _, c = _client(tmp_path / "runs")
    page = c.get("/runs/fixture-clean").text
    form = page[page.index('<form class="compare"'):]
    form = form[:form.index("</form>")]
    assert 'method="get" action="/compare"' in form and 'name="a" value="fixture-clean"' in form
    options = re.findall(r'<option value="([^"]+)"', form)
    assert sorted(options) == ["fixture-failed", "fixture-recovered"]  # every other run, not itself
    _seed(tmp_path / "solo", "clean")
    _, solo = _client(tmp_path / "solo")
    assert 'class="compare"' not in solo.get("/runs/fixture-clean").text  # nothing to compare with


def test_compare_hostile_scores_do_not_break_the_page(tmp_path):  # C1: files are untrusted
    d = _evaluated(tmp_path, "clean", "recovered")
    s = json.loads((d["recovered"] / "scores.json").read_text())
    s["workflow"]["components"] = {"<script>x</script>": "NaN", "ok": True}
    s["output"]["criteria"] = [{"id": "<b>crit</b>", "score": "4"}, "junk", {"id": "m", "score": 3, "max": 4}]
    (d["recovered"] / "scores.json").write_text(json.dumps(s))
    _, c = _client(tmp_path / "runs")
    r = c.get("/compare?a=fixture-clean&b=fixture-recovered")
    assert r.status_code == 200 and "<script>x</script>" not in r.text and "&lt;script&gt;" in r.text
    assert "<td>3/4</td>" in r.text


# --- increment 5: live run monitor ------------------------------------------------------------------


def _running(d):
    rj = json.loads((d / "run.json").read_text())
    rj["status"] = "running"
    rj.pop("ended_at", None)
    (d / "run.json").write_text(json.dumps(rj))


def test_live_fragment_and_script_only_while_live(tmp_path):  # L1, L2, L4
    runs = tmp_path / "runs"
    d = _seed(runs, "recovered")
    _, c = _client(runs)
    page = c.get("/runs/fixture-recovered").text
    assert "monitor.js" not in page and 'data-live="0"' in page  # finished: no script, no polling
    _running(d["recovered"])
    page = c.get("/runs/fixture-recovered").text
    assert '<script src="/static/monitor.js" defer></script>' in page and 'data-live="1"' in page
    assert '<noscript><meta http-equiv="refresh" content="3"></noscript>' in page and "data-start=" in page
    frag = c.get("/runs/fixture-recovered/live?seen=5")
    assert frag.status_code == 200 and frag.text.lstrip().startswith('<section id="monitor"')
    assert "<html" not in frag.text and "wf-bar working" in frag.text
    news = re.findall(r'class="feed-item[^"]*\bnew\b[^"]*">\s*<div class="feed-head">\s*<span class="step-no">(\d+)', frag.text)
    assert news and all(int(n) > 5 for n in news)
    assert " new" not in c.get("/runs/fixture-recovered").text.split('id="monitor"')[1].split("feed-item")[1][:60]


@pytest.mark.parametrize("path", ["/runs/a..b/live", "/runs/nope/live", "/runs/%2e%2e/live", "/runs/.ui-jobs/live"])
def test_live_fragment_bad_ids_404(tmp_path, path):  # L2
    _seed(tmp_path / "runs", "clean")
    _, c = _client(tmp_path / "runs")
    assert c.get(path).status_code == 404


def test_live_fragment_renders_untrusted_content_inert(tmp_path):  # L5
    runs = tmp_path / "runs"
    d = _seed(runs, "hostile")
    _running(d["hostile"])
    _, c = _client(runs)
    html = c.get("/runs/fixture-hostile/live").text
    assert "<script" not in html and "<img" not in html and "<b>" not in html
    assert "\\u003cscript\\u003e" in html and "&lt;b&gt;bold&lt;/b&gt;" in html  # args via tojson; error text escaped


def test_csp_and_static_allowlist(tmp_path):  # S1
    _, c = _client(tmp_path / "runs")
    csp = c.get("/").headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src 'self'" in csp and "connect-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp and "unsafe-eval" not in csp and "script-src 'unsafe-inline'" not in csp
    js = c.get("/static/monitor.js")
    assert js.status_code == 200 and js.headers["content-type"].startswith("text/javascript")
    assert "innerHTML" not in js.text and "eval(" not in js.text
    for bad in ("/static/app.py", "/static/..%2Fapp.py", "/static/monitor.js%00", "/static/"):
        assert c.get(bad).status_code == 404


def test_feed_renders_model_markdown_safely(tmp_path):  # increment 5 follow-up: model text as Markdown, inert
    runs = tmp_path / "runs"
    d = _seed(runs, "recovered")
    trace = d["recovered"] / "trace.jsonl"
    last = max(json.loads(ln)["step"] for ln in trace.read_text().splitlines() if ln.strip())
    text = ("## Plan\n\n| Dataset | Res |\n|---|---|\n| **NAIP** | 0.6 m |\n\n<script>alert(1)</script> "
            "[x](javascript:alert(1)) ![i](https://evil.example/i.png)")
    with open(trace, "a") as fh:
        fh.write(json.dumps({"step": last + 1, "type": "llm_call", "name": "model", "status": "ok",
                             "started_at": "2026-10-29T14:03:00.000Z", "duration_ms": 10, "text": text}) + "\n")
    _, c = _client(runs)
    html = c.get("/runs/fixture-recovered/live").text
    feed = html.split('class="feed-md wrap">')[1].split("</div>")[0]  # newest first: the added step
    assert "<h2>Plan</h2>" in feed and "<table>" in feed and "<strong>NAIP</strong>" in feed
    assert "<script" not in feed and "&lt;script&gt;" in feed
    assert 'href="javascript' not in feed.lower() and "<img" not in feed


def test_pipeline_run_page_compare_and_cards(tmp_path):  # increment 7: U1, U2, S4 through the real runner
    from harness_helpers import PROMPT
    from harness_helpers import load as load_cfg

    from foundry.harness.runner import run as run_agent

    turns = [{"text": "1. Search."}, {"tool": "echo", "input": {"text": "hi"}}, {"text": "## Plan\nUse NAIP."},
             {"text": "VERDICT: approve"}]
    cfg = load_cfg(write_config(tmp_path / "cfg", turns, extra='\n[agents]\nmode = "pipeline"\n'))
    runs = tmp_path / "runs"
    pipe_dir, status = run_agent(cfg, PROMPT, runs)
    assert status == "succeeded"
    _seed(runs, "clean")
    _, c = _client(runs)
    html = c.get(f"/runs/{pipe_dir.name}").text
    assert html.count('class="wf-row agent') == 3 and "<strong>executor</strong>" in html and "✓ approve" in html
    assert 'class="pill agent-pill">executor</span>' in html and "<strong>▸ checker</strong>" in html
    assert 'class="wf-row indent' in html
    cmp = c.get(f"/compare?a={pipe_dir.name}&b=fixture-clean").text
    assert "scripted-test · pipeline" in cmp and "<th>Agent</th>" in cmp and "<td>executor</td>" in cmp
    index = c.get("/").text
    assert "scripted-test · pipeline" in index
    assert pipe_dir.name in c.get("/?model=scripted-test · pipeline").text


# --- increment 8: rubric import, review, activation, tracking ----------------------------------------------------

RUBRIC = {"rubric_id": "clay-adb", "version": "1.0", "task_id": "adb", "description": "From <b>Clay</b>.",
          "criteria": [{"id": "accuracy", "name": "Accuracy <script>x</script>", "description": "Faithful.", "max": 3,
                        "weight": 2, "levels": {"1": "Wrong", "3": "Right"}}]}


def _rubric_project(tmp_path):
    root = tmp_path / "proj"
    shutil.copytree(ROOT / "tasks", root / "tasks")
    shutil.copytree(ROOT / "config", root / "config")
    return root


def _upload(c, app, name, data, **form):
    return c.post("/rubric/draft", data={"csrf": _csrf(app), "task": "adb", "kind": "file", "config": "offline", **form},
                  files={"file": (name, data)})


def test_rubric_import_review_activate_rollback(tmp_path):  # R1-R5, T2 through the real app
    root = _rubric_project(tmp_path)
    app, c = _client(tmp_path / "runs", root=root)
    assert 'aria-current="page">Rubric' in c.get("/rubric").text
    r = _upload(c, app, "clay.json", json.dumps(RUBRIC).encode())  # I5: already a rubric, no model needed
    assert r.status_code == 303 and "/rubric/drafts/" in r.headers["location"]
    draft_url = r.headers["location"].split("?")[0]
    page = c.get(r.headers["location"]).text
    assert "✓ Valid" in page and "Accuracy &lt;script&gt;x&lt;/script&gt;" in page and "<script>x" not in page
    form = {"csrf": _csrf(app), "task": "adb", "rubric_id": "clay-adb", "version": "1.0", "description": "d",
            "count": "1", "c0-id": "accuracy", "c0-name": "Accuracy", "c0-description": "Faithful.", "c0-max": "3",
            "c0-weight": "2", "c0-level-1": "Wrong", "c0-level-3": "Right"}
    page = c.post(draft_url, data={**form, "action": "add"}).text
    assert "Criterion 2" in page and "✗ Invalid" in page  # the empty new card fails validation, in text
    page = c.post(draft_url, data={**form, "count": "2", "action": "remove-1"}).text
    assert "Criterion 2" not in page and "✓ Valid" in page
    r = c.post(draft_url, data={**form, "action": "activate"})
    assert r.status_code == 303
    assert json.loads((root / "tasks/adb/rubric.json").read_text())["rubric_id"] == "clay-adb"
    tab = c.get("/rubric").text
    assert "clay-adb" in tab and "✓ active" in tab and "adb-rubric-v0" in tab and "clay.json" in tab
    r = c.post("/rubric/history/activate", data={"csrf": _csrf(app), "task": "adb", "file": "adb-rubric-v0@0.1.json"})
    assert r.status_code == 303
    assert json.loads((root / "tasks/adb/rubric.json").read_text())["rubric_id"] == "adb-rubric-v0"
    assert c.get("/rubric/history/clay-adb@1.0.json?task=adb").status_code == 200


def test_rubric_routes_deny(tmp_path):  # S1, S1b
    root = _rubric_project(tmp_path)
    app, c = _client(tmp_path / "runs", root=root)
    assert c.post("/rubric/draft", data={"csrf": "nope", "task": "adb", "kind": "text", "text": "x"}).status_code == 403
    assert _upload(c, app, "evil.exe", b"MZ").status_code == 400
    assert _upload(c, app, "big.pdf", b"x" * 4_600_000).status_code == 413  # enforced while streaming
    assert "needs a Bedrock judge model" in c.post("/rubric/draft", data={
        "csrf": _csrf(app), "task": "adb", "kind": "text", "text": "accuracy 0-3", "config": "offline"}).text
    # Filled the text box but left the radio on File: the kind is inferred instead of "Choose a file".
    assert "needs a Bedrock judge model" in c.post("/rubric/draft", data={
        "csrf": _csrf(app), "task": "adb", "kind": "file", "text": "accuracy 0-3", "config": "offline"}).text
    assert c.post("/rubric/draft", data={"csrf": _csrf(app), "task": "../etc", "kind": "text", "text": "x",
                                         "config": "offline"}).status_code == 400
    r = c.post("/rubric/draft", data={"csrf": _csrf(app), "task": "adb", "kind": "url",
                                      "url": "https://169.254.169.254/latest", "config": "offline"})
    assert r.status_code == 400 and "non-public" in r.text
    for bad in ("../rubric.json", "x.json", "a@b.json%2F..%2F..%2Fc"):
        assert c.post("/rubric/history/activate", data={"csrf": _csrf(app), "task": "adb", "file": bad}).status_code == 404
        assert c.get(f"/rubric/history/{bad}?task=adb").status_code == 404
    for bad in ("../../etc", "ABCDEF123456", "0" * 13):
        assert c.get(f"/rubric/drafts/{bad}?task=adb").status_code == 404
        assert c.get(f"/rubric/drafts/{bad}/source?task=adb").status_code == 404
    r = _upload(c, app, "clay.json", json.dumps({**RUBRIC, "rubric_id": "../escape"}).encode())
    assert r.status_code == 502  # not a valid rubric, so it needs the model; offline can't draft
    draft_url = _upload(c, app, "ok.json", json.dumps(RUBRIC).encode()).headers["location"].split("?")[0]
    r = c.post(draft_url, data={"csrf": _csrf(app), "task": "adb", "action": "activate", "rubric_id": "../escape",
                                "version": "1.0", "description": "d", "count": "1", "c0-id": "a", "c0-name": "A",
                                "c0-description": "d", "c0-max": "3", "c0-weight": "1", "c0-level-1": "x",
                                "c0-level-3": "y"})
    assert r.status_code == 400 and "Not activated" in r.text and "letters, digits" in r.text
    assert not list((root / "tasks").rglob("escape*"))
    import os

    good = _upload(c, app, "ok.json", json.dumps(RUBRIC).encode()).headers["location"].split("?")[0]
    (root / "tasks/adb/rubrics/.rubric.lock").write_text(str(os.getpid()))  # a live activation in progress
    form = {"csrf": _csrf(app), "task": "adb", "action": "activate", "rubric_id": "clay-adb", "version": "1.0",
            "description": "d", "count": "1", "c0-id": "a", "c0-name": "A", "c0-description": "d", "c0-max": "3",
            "c0-weight": "1", "c0-level-1": "x", "c0-level-3": "y"}
    r = c.post(good, data=form)
    assert r.status_code == 409 and "in progress" in r.text
    src = c.get(good + "/source?task=adb")
    assert src.headers["content-disposition"].startswith("attachment") and src.headers["x-content-type-options"] == "nosniff"


def test_run_page_names_the_rubric_that_scored_it(tmp_path):  # T1
    runs = tmp_path / "runs"
    d = _seed(runs, "clean")
    evaluate(d["clean"], load(eval_config(tmp_path / "e")))
    _, c = _client(runs)
    assert "Rubric:\n    adb-rubric-v0 v0.1" in c.get("/runs/fixture-clean").text
