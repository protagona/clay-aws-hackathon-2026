"""Local UI: score cards, report pages, and starting runs/evaluations as `foundry` subprocesses.

Bound to 127.0.0.1 only. Every request's Host is checked (DNS rebinding); every POST needs a same-origin
Origin (when sent) and the per-process CSRF token. At most one UI-started run at a time.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from foundry.config import ConfigError, load_config
from foundry.contract import evaluation_interrupted
from foundry.evaluate import lock
from foundry.runstore import RunDir, is_valid_run_id, new_run_id
from foundry.tasks import load_prompts
from foundry.ui.render import render

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
CONFIG_STEM_RE = re.compile(r"^[A-Za-z0-9._-]+$")
JOB_ID_RE = re.compile(r"^[a-f0-9]{16}$")


@dataclass
class Job:
    id: str
    kind: str  # "run" | "evaluate"
    run_id: str
    label: str
    proc: subprocess.Popen
    log: Path
    exit_code: int | None = None
    dismissed: bool = False
    lock_token: str | None = None

    @property
    def alive(self) -> bool:
        return self.exit_code is None

    @property
    def failed_visibly(self) -> bool:
        """Runs that exit 1 are ordinary failed runs with a card; only exit 2 (nothing created) needs a banner."""
        if self.exit_code is None or self.dismissed:
            return False
        return self.exit_code == 2 if self.kind == "run" else self.exit_code != 0

    def last_stderr_line(self) -> str:
        try:
            lines = [ln for ln in self.log.read_text(errors="replace").splitlines() if ln.strip()]
        except FileNotFoundError:
            return ""
        return lines[-1][:300] if lines else f"exited with code {self.exit_code}"


@dataclass
class UiState:
    runs_dir: Path
    root: Path
    port: int
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    jobs: dict[str, Job] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


def create_app(runs_dir: str | Path, project_root: str | Path, port: int = 8765) -> FastAPI:
    st = UiState(Path(runs_dir).resolve(), Path(project_root).resolve(), port)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ui = st
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host", "") not in allowed_hosts:
            return PlainTextResponse("Bad Host header", status_code=400)
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin is not None and origin != f"http://{request.headers['host']}":
                return PlainTextResponse("Cross-origin request refused", status_code=403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def check_csrf(token: str) -> Response | None:
        if not hmac.compare_digest(token.encode(), st.csrf.encode()):
            return PlainTextResponse("Missing or invalid CSRF token; reload the page and try again", status_code=403)
        return None

    def page(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(request, name, {"csrf": st.csrf, **ctx}, status_code=status)

    def error(request: Request, status: int, message: str) -> HTMLResponse:
        return page(request, "error.html", status=status, message=message)

    def run_path(run_id: str) -> Path | None:
        if not is_valid_run_id(run_id):
            return None
        path = (st.runs_dir / run_id).resolve()
        if not path.is_relative_to(st.runs_dir) or not (path / "run.json").is_file():
            return None
        return path

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return page(request, "index.html", cards=_cards(st), configs=_configs(st), prompts=_prompt_ids(st),
                    banners=[j for j in st.jobs.values() if j.failed_visibly])

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str):
        path = run_path(run_id)
        if path is None:
            return error(request, 404, "Run not found")
        rd = RunDir(path)
        run = json.loads((path / "run.json").read_text())
        scores = _scores(path)
        interrupted = scores is not None and evaluation_interrupted(path)
        report_html = None
        if scores is not None and not interrupted and (path / "report.md").exists():
            report_html = render((path / "report.md").read_text())
        steps = []
        try:
            steps = rd.read_steps(drop_truncated_last=True)
        except ValueError:
            pass
        return page(request, "run.html", run=run, scores=None if interrupted else scores, report_html=report_html,
                    steps=steps, interrupted=interrupted, evaluating=lock.is_locked(path))

    @app.post("/runs")
    def start_run(request: Request, prompt_id: str = Form(""), config: str = Form(""), csrf: str = Form("")):
        if (denied := check_csrf(csrf)) is not None:
            return denied
        configs = _configs(st)
        if config not in configs:
            return error(request, 400, f"Unknown config '{config}'. Available: {', '.join(configs)}")
        try:
            cfg = load_config(st.root / "config" / f"{config}.toml")
            ids = [p.prompt_id for p in load_prompts(cfg.task.id)]
        except ConfigError as exc:
            return error(request, 400, str(exc))
        if prompt_id not in ids:
            return error(request, 400, f"Unknown prompt id '{prompt_id}' for task '{cfg.task.id}'. Available: {', '.join(ids)}")
        with st.lock:
            active = next((j for j in st.jobs.values() if j.kind == "run" and j.alive), None)
            if active is not None:
                return error(request, 409, f"A run is already in progress: {active.run_id}")
            run_id = new_run_id(prompt_id)
            _spawn(st, "run", run_id, f"Run {prompt_id}",
                   ["run", "--config", f"config/{config}.toml", "--prompt", prompt_id,
                    "--runs-dir", str(st.runs_dir), "--run-id", run_id])
        return RedirectResponse("/", status_code=303)

    @app.post("/runs/{run_id}/evaluate")
    def start_evaluate(request: Request, run_id: str, csrf: str = Form("")):
        if (denied := check_csrf(csrf)) is not None:
            return denied
        path = run_path(run_id)
        if path is None:
            return error(request, 404, "Run not found")
        run = json.loads((path / "run.json").read_text())
        if run.get("status") == "running":
            return error(request, 409, f"Run {run_id} is still running. If it crashed, evaluate it from the CLI with --force.")
        try:
            token = lock.acquire(path, owner="ui")
        except lock.LockHeld as exc:
            return error(request, 409, str(exc))
        config = _eval_config_for(st, run)
        with st.lock:
            _spawn(st, "evaluate", run_id, f"Evaluation of {run_id}",
                   ["evaluate", str(path), "--config", config, "--lock-held", token], lock_token=token, lock_dir=path)
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.get("/jobs/{job_id}/log", response_class=PlainTextResponse)
    def job_log(job_id: str):
        job = st.jobs.get(job_id) if JOB_ID_RE.match(job_id) else None
        if job is None:
            return PlainTextResponse("Job not found", status_code=404)
        return PlainTextResponse(job.log.read_text(errors="replace") if job.log.exists() else "")

    @app.post("/jobs/{job_id}/dismiss")
    def dismiss(job_id: str, csrf: str = Form("")):
        if (denied := check_csrf(csrf)) is not None:
            return denied
        job = st.jobs.get(job_id) if JOB_ID_RE.match(job_id) else None
        if job is not None:
            job.dismissed = True
        return RedirectResponse("/", status_code=303)

    return app


def _spawn(st: UiState, kind: str, run_id: str, label: str, args: list[str], lock_token: str | None = None,
           lock_dir: Path | None = None) -> Job:
    job_id = secrets.token_hex(8)
    log_dir = st.runs_dir / ".ui-jobs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"{job_id}.log"
    env = {**__import__("os").environ, "FOUNDRY_HOME": str(st.root)}
    with open(log, "wb") as errf:
        proc = subprocess.Popen([sys.executable, "-m", "foundry", *args], cwd=st.root, env=env,
                                stdout=subprocess.DEVNULL, stderr=errf, stdin=subprocess.DEVNULL)
    job = Job(job_id, kind, run_id, label, proc, log, lock_token=lock_token)
    st.jobs[job_id] = job

    def monitor():
        job.exit_code = proc.wait()
        if lock_token and lock_dir is not None:
            lock.release(lock_dir, lock_token)  # whatever the exit code; only if the token still matches

    threading.Thread(target=monitor, name=f"foundry-job-{job_id}", daemon=True).start()
    return job


def _configs(st: UiState) -> list[str]:
    return sorted(p.stem for p in (st.root / "config").glob("*.toml") if CONFIG_STEM_RE.match(p.stem))


def _prompt_ids(st: UiState) -> list[str]:
    ids: list[str] = []
    for stem in _configs(st):
        try:
            cfg = load_config(st.root / "config" / f"{stem}.toml")
            ids += [p.prompt_id for p in load_prompts(cfg.task.id) if p.prompt_id not in ids]
        except ConfigError:
            continue
    return ids


def _eval_config_for(st: UiState, run: dict) -> str:
    recorded = run.get("config") or ""
    stem = Path(recorded).stem
    if recorded and CONFIG_STEM_RE.match(stem) and (st.root / "config" / f"{stem}.toml").is_file():
        return f"config/{stem}.toml"
    return "config/offline.toml"


def _scores(path: Path) -> dict | None:
    try:
        return json.loads((path / "scores.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _short(score) -> str:
    return "—" if score is None else f"{score:.2f}".removeprefix("0")


def _when(iso: str | None) -> str:
    try:
        return datetime.strptime(iso.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z").strftime("%m-%d %H:%M")
    except (AttributeError, ValueError):
        return ""


def _cards(st: UiState) -> list[dict]:
    cards = []
    if not st.runs_dir.is_dir():
        return cards
    for d in st.runs_dir.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        try:
            run = json.loads((d / "run.json").read_text())
            run_id, prompt_id, model = run["run_id"], run["prompt_id"], run["model"]["id"]
        except (OSError, ValueError, KeyError, TypeError):
            cards.append({"unreadable": True, "name": d.name, "started_at": ""})
            continue
        scores = _scores(d)
        status = run.get("status", "?")
        if lock.is_locked(d):
            headline = "evaluating"
        elif status == "running":
            headline = "running"
        elif scores is None:
            headline = "not evaluated"
        elif evaluation_interrupted(d) or scores.get("overall") is None:
            headline = "incomplete"
        else:
            headline = f"{scores['overall']:.2f}"
        sub = ""
        if scores is not None and headline not in ("incomplete", "evaluating"):
            sub = f"W{_short(scores['workflow']['score'])} O{_short(scores['output']['score'])}"
        cards.append({"unreadable": False, "run_id": run_id, "prompt_id": prompt_id, "model": model,
                      "headline": headline, "sub": sub, "status": status, "when": _when(run.get("started_at")),
                      "footer": f"{run.get('task_id', '?')} · …{run_id[-4:]}", "started_at": run.get("started_at", "")})
    return sorted(cards, key=lambda c: c["started_at"], reverse=True)
