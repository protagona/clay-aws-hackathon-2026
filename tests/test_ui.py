"""UI (AC24-AC29) through the real FastAPI app; runs and evaluations are real `foundry` subprocesses."""

import json
import re
import shutil
import time

import pytest
from conftest import FIXTURES, ROOT
from eval_helpers import eval_config, judge_reply, load
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
    assert r.status_code == 200 and "<h2>Workflow score: 1.00</h2>" in r.text and "Overall 0.83" in r.text
    r = c.get("/runs/fixture-recovered")
    assert 'action="/runs/fixture-recovered/evaluate"' in r.text and "<table>" in r.text
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
    assert "<h2>Workflow score: 0.90</h2>" in c.get("/runs/fixture-recovered").text


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
