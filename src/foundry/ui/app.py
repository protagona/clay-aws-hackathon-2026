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
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
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
BATCH_ID_RE = re.compile(r"^b-\d{8}-\d{6}-[a-f0-9]{4}$")


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
        if self.kind in ("run", "batch"):  # exit 1 = ordinary failed run / budget stop, shown on its own page
            return self.exit_code == 2
        return self.exit_code != 0

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
    receiver_port: int = 4318
    receiver: object | None = None  # foundry.receiver.Receiver while running

    @property
    def batches_dir(self) -> Path:
        return self.runs_dir / ".batches"


def create_app(runs_dir: str | Path, project_root: str | Path, port: int = 8765, receiver_port: int = 4318) -> FastAPI:
    st = UiState(Path(runs_dir).resolve(), Path(project_root).resolve(), port, receiver_port=receiver_port)
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
        try:
            response = await call_next(request)
        except Exception:
            response = PlainTextResponse("Internal Server Error", status_code=500)
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
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
        if not is_valid_run_id(run_id) or run_id.startswith("."):
            return None
        raw = st.runs_dir / run_id
        if raw.is_symlink():
            return None
        path = raw.resolve()
        if not path.is_relative_to(st.runs_dir) or _read_run(path) is None:
            return None
        return path

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        cards = _cards(st)
        busy = any(c.get("headline") in ("running", "evaluating") for c in cards) or st.receiver is not None
        return page(request, "index.html", cards=cards, configs=_configs(st), prompts=_prompt_ids(st),
                    banners=[j for j in st.jobs.values() if j.failed_visibly], refresh=busy, nav="runs")

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str):
        path = run_path(run_id)
        if path is None:
            return error(request, 404, "Run not found")
        rd = RunDir(path)
        run = {**_read_run(path), "run_id": path.name}  # links and forms use the directory name, never run.json's
        scores = _scores(path)
        interrupted = scores is not None and evaluation_interrupted(path)
        report_html = None
        if scores is not None and not interrupted and _regular(path / "report.md"):
            report_html = render((path / "report.md").read_text())
        steps = []
        try:
            if _regular(path / "trace.jsonl"):
                steps = rd.read_steps(drop_truncated_last=True)
        except ValueError:
            pass
        evaluating = lock.is_locked(path)
        return page(request, "run.html", run=run, scores=None if interrupted else scores, report_html=report_html,
                    steps=steps, interrupted=interrupted, evaluating=evaluating,
                    refresh=evaluating or run.get("status") == "running")

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
        run = _read_run(path)
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

    # --- batches -----------------------------------------------------------------------------------

    @app.get("/batches", response_class=HTMLResponse)
    def batches(request: Request):
        running = any(j.kind == "batch" and j.alive for j in st.jobs.values())
        return page(request, "batches.html", nav="batches", batches=_batch_list(st), configs=_configs(st),
                    models=_model_choices(st), prompts=_prompt_ids(st), running=running, refresh=running)

    @app.post("/batches")
    def start_batch(request: Request, config: str = Form(""), models: list[str] = Form(default=[]),
                    prompts: list[str] = Form(default=[]), repeats: int = Form(1), concurrency: int = Form(2),
                    evaluate: str = Form(""), max_cost: float = Form(2.0), csrf: str = Form("")):
        if (denied := check_csrf(csrf)) is not None:
            return denied
        if config not in _configs(st):
            return error(request, 400, f"Unknown config '{config}'. Available: {', '.join(_configs(st))}")
        unknown = [m for m in models if m not in _model_choices(st)]
        if unknown:
            return error(request, 400, f"Unknown model(s): {', '.join(unknown)}")
        prompt_ids = _prompt_ids(st)
        if not prompts or any(p not in prompt_ids for p in prompts):
            return error(request, 400, f"Pick one or more prompts from: {', '.join(prompt_ids)}")
        if not (1 <= repeats <= 5 and 1 <= concurrency <= 4 and 0 < max_cost <= 50):
            return error(request, 400, "Repeats must be 1-5, parallel runs 1-4, and the cost cap between $0 and $50.")
        with st.lock:
            active = next((j for j in st.jobs.values() if j.kind == "batch" and j.alive), None)
            if active is not None:
                return error(request, 409, f"A batch is already running: {active.run_id}")
            batch_id = time.strftime("b-%Y%m%d-%H%M%S-") + secrets.token_hex(2)
            args = ["batch", "--config", f"config/{config}.toml", "--prompts", ",".join(prompts),
                    "--repeats", str(repeats), "--concurrency", str(concurrency), "--max-cost", f"{max_cost:.2f}",
                    "--out", str(st.batches_dir / batch_id), "--runs-dir", str(st.runs_dir),
                    "--run-prefix", f"{batch_id}_"]
            if models:
                args += ["--models", ",".join(models)]
            if evaluate:
                args.append("--evaluate")
            _spawn(st, "batch", batch_id, f"Batch {batch_id}", args)
        return RedirectResponse(f"/batches/{batch_id}", status_code=303)

    @app.get("/batches/{batch_id}", response_class=HTMLResponse)
    def batch_page(request: Request, batch_id: str):
        path = st.batches_dir / batch_id
        if not BATCH_ID_RE.match(batch_id) or not path.is_dir():
            return error(request, 404, "Batch not found")
        job = next((j for j in st.jobs.values() if j.kind == "batch" and j.run_id == batch_id), None)
        meta = _read_json(path / "batch.json") or {}
        rows = _batch_rows(st, path)
        running = (job is not None and job.alive) or (job is None and meta.get("status") == "running"
                                                       and not meta.get("exit_code"))
        if job is not None and not job.alive and meta.get("status") != "finished":
            meta["status"] = "failed to start" if job.exit_code == 2 else "stopped"
        return page(request, "batch.html", nav="batches", batch_id=batch_id, meta=meta, rows=rows,
                    variants=_variant_stats(rows), job=job, running=running, refresh=running,
                    done=sum(r["status"] not in ("pending", "") for r in rows))

    # --- import & receiver ---------------------------------------------------------------------------

    @app.get("/import", response_class=HTMLResponse)
    def import_page(request: Request):
        rec = st.receiver
        return page(request, "import.html", nav="import", tasks=_task_ids(st), configs=_configs(st),
                    receiver=rec, received=[p.name for p in getattr(rec, "written", [])][-10:],
                    refresh=rec is not None)

    @app.post("/import")
    async def do_import(request: Request, file: UploadFile = File(...), task: str = Form("adb"),
                        prompt_id: str = Form("imported"), config: str = Form(""), csrf: str = Form("")):
        from foundry.importer import MAX_FILE_BYTES, ImportError_, import_spans, spans_from_file

        if (denied := check_csrf(csrf)) is not None:
            return denied
        if task not in _task_ids(st) or not is_valid_run_id(prompt_id) or (config and config not in _configs(st)):
            return error(request, 400, "Pick a task, a prompt id of letters/digits/._- and a known config.")
        upload_dir = st.runs_dir / ".uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest = upload_dir / f"{secrets.token_hex(8)}.json"
        size = 0
        with open(dest, "wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    out.close()
                    dest.unlink(missing_ok=True)
                    return error(request, 413, f"File is over the {MAX_FILE_BYTES // (1 << 20)} MB limit.")
                out.write(chunk)
        try:
            prices = load_config(st.root / "config" / f"{config}.toml").prices if config else None
            paths = import_spans(spans_from_file(dest), st.runs_dir, task, prompt_id,
                                 f"upload:{Path(file.filename or 'trace').name}", prices)
        except (ImportError_, ConfigError) as exc:
            return error(request, 400, f"Could not import {file.filename}: {exc}")
        return RedirectResponse(f"/runs/{paths[0].name}" if len(paths) == 1 else "/", status_code=303)

    @app.post("/receiver/start")
    def receiver_start(request: Request, task: str = Form("adb"), prompt_id: str = Form("imported"),
                       config: str = Form(""), csrf: str = Form("")):
        from foundry.receiver import Receiver

        if (denied := check_csrf(csrf)) is not None:
            return denied
        if task not in _task_ids(st) or not is_valid_run_id(prompt_id) or (config and config not in _configs(st)):
            return error(request, 400, "Pick a task, a prompt id of letters/digits/._- and a known config.")
        with st.lock:
            if st.receiver is None:
                try:
                    prices = load_config(st.root / "config" / f"{config}.toml").prices if config else None
                    st.receiver = Receiver(st.runs_dir, task, prompt_id, prices, port=st.receiver_port,
                                           log=lambda *_: None).start()
                except OSError as exc:
                    return error(request, 409, f"Cannot listen on 127.0.0.1:{st.receiver_port}: {exc.strerror}. "
                                               "Is another receiver running?")
        return RedirectResponse("/import", status_code=303)

    @app.post("/receiver/stop")
    def receiver_stop(csrf: str = Form("")):
        if (denied := check_csrf(csrf)) is not None:
            return denied
        with st.lock:
            if st.receiver is not None:
                st.receiver.close()
                st.receiver = None
        return RedirectResponse("/import", status_code=303)

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


def _read_json(path: Path) -> dict | None:
    if not _regular(path):
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _model_choices(st: UiState) -> list[str]:
    """Models offered for batches: every agent model and priced model in the configs (an allowlist)."""
    out: set[str] = set()
    for stem in _configs(st):
        try:
            cfg = load_config(st.root / "config" / f"{stem}.toml")
        except ConfigError:
            continue
        if cfg.model.provider == "bedrock":
            out.add(cfg.model.id)
            out.update(cfg.prices)
    return sorted(out)


def _task_ids(st: UiState) -> list[str]:
    return sorted(p.parent.name for p in (st.root / "tasks").glob("*/prompts.jsonl") if CONFIG_STEM_RE.match(p.parent.name))


def _batch_list(st: UiState) -> list[dict]:
    if not st.batches_dir.is_dir():
        return []
    out = []
    for d in st.batches_dir.iterdir():
        if not (d.is_dir() and BATCH_ID_RE.match(d.name)):
            continue
        meta = _read_json(d / "batch.json") or {}
        rows = _batch_rows(st, d)
        out.append({"id": d.name, "status": meta.get("status", "starting"), "jobs": meta.get("jobs", len(rows)),
                    "done": sum(r["status"] not in ("pending", "") for r in rows),
                    "spent": sum(r["cost"] for r in rows if isinstance(r["cost"], float)),
                    "max_cost": meta.get("max_cost"), "models": meta.get("models") or meta.get("configs") or []})
    return sorted(out, key=lambda b: b["id"], reverse=True)


def _batch_rows(st: UiState, path: Path) -> list[dict]:
    import csv

    f = path / "summary.csv"
    if not _regular(f):
        return []
    rows = []
    with open(f, newline="") as fh:
        for r in csv.DictReader(fh):
            run_dir = Path(r.get("run_dir") or "")
            link = run_dir.name if r.get("run_dir") and (st.runs_dir / run_dir.name).is_dir() \
                and is_valid_run_id(run_dir.name) else None
            rows.append({**r, "link": link, "cost": _num(_float(r.get("cost_usd"))),
                         "overall_f": _num(_float(r.get("overall")))})
    return rows


def _float(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _variant_stats(rows: list[dict]) -> list[dict]:
    out = []
    for name in dict.fromkeys(r["variant"] for r in rows):
        rs = [r for r in rows if r["variant"] == name]
        ran = [r for r in rs if r.get("run_dir")]
        overall = [r["overall_f"] for r in ran if r["overall_f"] is not None]
        costs = [r["cost"] for r in ran if r["cost"] is not None]
        out.append({"variant": name, "model": rs[0].get("model"), "succeeded": sum(r["status"] == "succeeded" for r in ran),
                    "total": len(rs), "overall": sum(overall) / len(overall) if overall else None,
                    "cost": sum(costs) / len(costs) if costs else None})
    return out


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


def _regular(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _read_run(path: Path) -> dict | None:
    """run.json as a dict with the fields the UI shows, or None if it's missing, a symlink, or malformed."""
    f = path / "run.json"
    if not _regular(f):
        return None
    try:
        run = json.loads(f.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(run, dict) or not isinstance(run.get("model"), dict):
        return None
    if not all(isinstance(run.get(k), str) for k in ("prompt_id", "status")):
        return None
    return run


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _scores(path: Path) -> dict | None:
    """scores.json reduced to the numbers the UI shows; None if missing, a symlink, or malformed."""
    f = path / "scores.json"
    if not _regular(f):
        return None
    try:
        raw = json.loads(f.read_text())
        return {**raw, "overall": _num(raw.get("overall")),
                "workflow": {**raw["workflow"], "score": _num(raw["workflow"].get("score"))},
                "output": {**raw["output"], "score": _num(raw["output"].get("score"))}}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
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
        if d.is_symlink() or not d.is_dir() or d.name.startswith(".") or not is_valid_run_id(d.name):
            continue
        run = _read_run(d)
        if run is None:
            cards.append({"unreadable": True, "name": d.name, "started_at": ""})
            continue
        scores = _scores(d)
        status = run["status"]
        if lock.is_locked(d):
            headline = "evaluating"
        elif status == "running":
            headline = "running"
        elif scores is None:
            headline = "not evaluated" if not (d / "scores.json").exists() else "incomplete"
        elif evaluation_interrupted(d) or scores["overall"] is None:
            headline = "incomplete"
        else:
            headline = f"{scores['overall']:.2f}"
        sub = ""
        if scores is not None and headline not in ("incomplete", "evaluating") and scores["workflow"]["score"] is not None:
            sub = f"W{_short(scores['workflow']['score'])} O{_short(scores['output']['score'])}"
        started = run.get("started_at") if isinstance(run.get("started_at"), str) else ""
        cards.append({"unreadable": False, "run_id": d.name, "prompt_id": run["prompt_id"],
                      "model": str(run["model"].get("id", "?")), "headline": headline, "sub": sub, "status": status,
                      "when": _when(started), "footer": f"{run.get('task_id', '?')} · …{d.name[-4:]}",
                      "started_at": started})
    return sorted(cards, key=lambda c: c["started_at"], reverse=True)
