"""Local UI: score cards, report pages, and starting runs/evaluations as `foundry` subprocesses.

Bound to 127.0.0.1 only. Every request's Host is checked (DNS rebinding); every POST needs a same-origin
Origin (when sent) and the per-process CSRF token. At most one UI-started run at a time.
"""

from __future__ import annotations

import hmac
import re
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from foundry.engine.batch import spread
from foundry.config import ConfigError, load_config
from foundry.contract import evaluation_interrupted
from foundry.data import locks as lock
from foundry.data import batches as data_batches
from foundry.data import project as data_project
from foundry.data import runs as data_runs
from foundry.data.batches import BATCH_ID_RE  # noqa: F401 (still read as core.BATCH_ID_RE until slice 5)
from foundry.data.project import CONFIG_STEM_RE
from foundry.data.runs import RunDir
from foundry.data.tasks import parse_prompts
from foundry.ui import dashboard
from foundry.ui.render import render

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
TEMPLATES.env.filters["markdown"] = lambda text: Markup(render(text[:FEED_TEXT_MAX]))  # same safe renderer as reports
FEED_TEXT_MAX = 20_000  # characters of a model step rendered in the feed
JOB_ID_RE = re.compile(r"^[a-f0-9]{16}$")
STATIC = {"monitor.js": "text/javascript"}  # the only files /classic/static serves
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
       "img-src 'self' data:; font-src 'self'; form-action 'self'; frame-ancestors 'none'")


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
    finished_at: float | None = None  # time.time() when the process exited (toasts in the TypeScript UI)
    lock_token: str | None = None
    prompts: list[str] | None = None  # a batch's prompts, so their text stays locked while it runs (increment 13)

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
    receiver: object | None = None  # foundry.engine.receiver.Receiver while running

    @property
    def batches_dir(self) -> Path:
        return self.runs_dir / ".batches"


CLASSIC = "/classic"  # the server-rendered UI (increments 1-8), kept as a fallback beside the TypeScript app


def create_app(runs_dir: str | Path, project_root: str | Path, port: int = 8765, receiver_port: int = 4318) -> FastAPI:
    from foundry.ui import actions as act
    from foundry.ui.actions import UiError

    st = UiState(Path(runs_dir).resolve(), Path(project_root).resolve(), port, receiver_port=receiver_port)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ui = st
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        api = request.url.path.startswith("/api/")

        def refuse(status: int, message: str) -> Response:
            return JSONResponse({"error": message}, status_code=status) if api else PlainTextResponse(message, status_code=status)

        if request.headers.get("host", "") not in allowed_hosts:
            response = refuse(400, "Bad Host header")
        elif request.method in ("POST", "PUT", "PATCH", "DELETE") and (
                request.headers.get("origin") or f"http://{request.headers['host']}") != f"http://{request.headers['host']}":
            response = refuse(403, "Cross-origin request refused")
        else:
            try:
                response = await call_next(request)
            except Exception:
                response = refuse(500, "Internal Server Error")
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Not "no-referrer": with it, browsers send `Origin: null` on form POSTs (Fetch spec), which the Origin
        # check above rejects. "same-origin" still sends nothing to other sites.
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    ui = APIRouter(prefix=CLASSIC)

    def check_csrf(token: str) -> Response | None:
        if not hmac.compare_digest(token.encode(), st.csrf.encode()):
            return PlainTextResponse("Missing or invalid CSRF token; reload the page and try again", status_code=403)
        return None

    def page(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(request, name, {"csrf": st.csrf, "P": CLASSIC, **ctx}, status_code=status)

    def error(request: Request, status: int, message: str) -> HTMLResponse:
        return page(request, "error.html", status=status, message=message)

    def go(path: str) -> RedirectResponse:
        return RedirectResponse(CLASSIC + path, status_code=303)

    @ui.get("/", response_class=HTMLResponse)
    def index(request: Request, model: str = ""):
        cards = _cards(st)
        busy = any(c.get("headline") in ("running", "evaluating") for c in cards) or st.receiver is not None
        if model:  # the dashboard leaderboard links here
            cards = [c for c in cards if not c["unreadable"] and c["model"] == model]
        return page(request, "index.html", cards=cards, configs=_configs(st), prompts=_prompt_ids(st),
                    banners=[j for j in st.jobs.values() if j.failed_visibly], refresh=busy, nav="runs",
                    model_filter=model)

    @ui.get("/dashboard", response_class=HTMLResponse)
    def dashboard_page(request: Request, rubric: str = "active"):
        return page(request, "dashboard.html", nav="dashboard", **act.dashboard_view(st, rubric))

    @ui.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str):
        path = act.run_path(st, run_id)
        if path is None:
            return error(request, 404, "Run not found")
        ctx = act.run_view(st, path)
        others = [c for c in sorted((c for c in _cards(st) if not c["unreadable"]), key=lambda c: c["started_at"],
                                    reverse=True) if c["run_id"] != path.name][:COMPARE_CHOICES]
        return page(request, "run.html", others=others, noscript_refresh=ctx["m"]["live"], **ctx)

    @ui.get("/runs/{run_id}/live", response_class=HTMLResponse)
    def run_live(request: Request, run_id: str, seen: int = 0):
        """The #monitor fragment alone, polled by monitor.js while the run is live."""
        path = act.run_path(st, run_id)
        if path is None:
            return PlainTextResponse("Run not found", status_code=404)
        return page(request, "_monitor.html", **act.run_view(st, path, seen=seen))

    @ui.get("/static/{name}")
    def static(name: str):
        if name not in STATIC:
            return PlainTextResponse("Not found", status_code=404)
        return Response((Path(__file__).with_name("static") / name).read_bytes(), media_type=STATIC[name],
                        headers={"Cache-Control": "no-cache"})

    @ui.get("/compare", response_class=HTMLResponse)
    def compare_page(request: Request, a: str = "", b: str = ""):
        try:
            sa, sb, rows = act.compare(st, a, b)
        except UiError as exc:
            return error(request, exc.status, exc.message)
        return page(request, "compare.html", nav="runs", a=sa, b=sb, rows=rows)

    @ui.post("/runs")
    def start_run(request: Request, prompt_id: str = Form(""), config: str = Form(""), csrf: str = Form("")):
        if (denied := check_csrf(csrf)) is not None:
            return denied
        try:
            act.start_run(st, prompt_id, config)
        except UiError as exc:
            return error(request, exc.status, exc.message)
        return go("/")

    @ui.post("/runs/{run_id}/evaluate")
    def start_evaluate(request: Request, run_id: str, csrf: str = Form("")):
        if (denied := check_csrf(csrf)) is not None:
            return denied
        try:
            act.start_evaluate(st, run_id)
        except UiError as exc:
            return error(request, exc.status, exc.message)
        return go(f"/runs/{run_id}")

    # --- batches -----------------------------------------------------------------------------------

    @ui.get("/batches", response_class=HTMLResponse)
    def batches(request: Request):
        running = any(j.kind == "batch" and j.alive for j in st.jobs.values())
        return page(request, "batches.html", nav="batches", batches=_batch_list(st), configs=_configs(st),
                    models=_model_choices(st), prompts=_prompt_ids(st), running=running, refresh=running)

    @ui.post("/batches")
    def start_batch(request: Request, config: str = Form(""), models: list[str] = Form(default=[]),
                    prompts: list[str] = Form(default=[]), repeats: int = Form(1), concurrency: int = Form(2),
                    evaluate: str = Form(""), max_cost: float = Form(2.0), csrf: str = Form("")):
        if (denied := check_csrf(csrf)) is not None:
            return denied
        try:
            batch_id = act.start_batch(st, config, models, prompts, repeats, concurrency, bool(evaluate), max_cost)
        except UiError as exc:
            return error(request, exc.status, exc.message)
        return go(f"/batches/{batch_id}")

    @ui.get("/batches/{batch_id}", response_class=HTMLResponse)
    def batch_page(request: Request, batch_id: str):
        try:
            view = act.batch_view(st, batch_id)
        except UiError as exc:
            return error(request, exc.status, exc.message)
        return page(request, "batch.html", nav="batches", refresh=view["running"], **view)

    # --- import & receiver ---------------------------------------------------------------------------

    @ui.get("/import", response_class=HTMLResponse)
    def import_page(request: Request):
        rec = st.receiver
        return page(request, "import.html", nav="import", tasks=_task_ids(st), configs=_configs(st),
                    receiver=rec, received=[p.name for p in getattr(rec, "written", [])][-10:],
                    refresh=rec is not None)

    @ui.post("/import")
    async def do_import(request: Request, file: UploadFile = File(...), task: str = Form("adb"),
                        prompt_id: str = Form("imported"), config: str = Form(""), csrf: str = Form("")):
        if (denied := check_csrf(csrf)) is not None:
            return denied
        try:
            act.check_import_form(st, task, prompt_id, config)
            dest = await save_upload(st, file)
            paths = act.import_saved_trace(st, dest, file.filename or "", task, prompt_id, config)
        except UiError as exc:
            return error(request, exc.status, exc.message)
        return go(f"/runs/{paths[0].name}" if len(paths) == 1 else "/")

    @ui.post("/receiver/start")
    def receiver_start(request: Request, task: str = Form("adb"), prompt_id: str = Form("imported"),
                       config: str = Form(""), csrf: str = Form("")):
        if (denied := check_csrf(csrf)) is not None:
            return denied
        try:
            act.receiver_start(st, task, prompt_id, config)
        except UiError as exc:
            return error(request, exc.status, exc.message)
        return go("/import")

    @ui.post("/receiver/stop")
    def receiver_stop(csrf: str = Form("")):
        if (denied := check_csrf(csrf)) is not None:
            return denied
        act.receiver_stop(st)
        return go("/import")

    # --- rubric import (increment 8) ------------------------------------------------------------------------

    @ui.get("/rubric", response_class=HTMLResponse)
    def rubric_page(request: Request, task: str = "adb"):
        try:
            view = act.rubric_overview(st, task)
        except UiError as exc:
            return error(request, exc.status, exc.message)
        return page(request, "rubric.html", nav="rubric", configs=_configs(st), **view)

    @ui.post("/rubric/draft")
    async def rubric_draft(request: Request, csrf: str = Form(""), task: str = Form("adb"), kind: str = Form("file"),
                           text: str = Form(""), url: str = Form(""), mcp: str = Form(""), args: str = Form(""),
                           config: str = Form("bedrock"), file: UploadFile | None = File(None)):
        from starlette.concurrency import run_in_threadpool

        if (denied := check_csrf(csrf)) is not None:
            return denied
        try:
            name, data = await read_rubric_upload(file)
            draft_id = await run_in_threadpool(act.rubric_create_draft, st, task, kind, config, file_name=name,
                                               file_data=data, text=text, url=url, mcp=mcp, args=args)
        except UiError as exc:
            return error(request, exc.status, exc.message)
        return go(f"/rubric/drafts/{draft_id}?task={task}")

    def draft_view(request: Request, task: str, draft_id: str, message: str | None = None, status: int = 200):
        try:
            view = act.rubric_draft(st, task, draft_id)
        except UiError as exc:
            return error(request, exc.status, exc.message)
        return page(request, "rubric_draft.html", status=status, nav="rubric", message=message, **view)

    @ui.get("/rubric/drafts/{draft_id}", response_class=HTMLResponse)
    def rubric_draft_page(request: Request, draft_id: str, task: str = "adb"):
        return draft_view(request, task, draft_id)

    @ui.post("/rubric/drafts/{draft_id}")
    async def rubric_draft_post(request: Request, draft_id: str):
        form = await request.form()
        if (denied := check_csrf(str(form.get("csrf", "")))) is not None:
            return denied
        task, action = str(form.get("task", "adb")), str(form.get("action", "save"))
        try:
            if action == "discard":  # before loading: an unreadable draft must still be discardable
                act.rubric_discard(st, task, draft_id)
                return go(f"/rubric?task={task}")
            body = _draft_from_form(form)
            if action == "add":
                body["criteria"].append({"id": "", "name": "", "description": "", "max": 4, "weight": 1, "levels": {}})
            elif action.startswith("remove-") and action[7:].isdigit() and int(action[7:]) < len(body["criteria"]):
                body["criteria"].pop(int(action[7:]))
            err = act.rubric_update_draft(st, task, draft_id, body, activate=action == "activate")
            if action == "activate":
                return go(f"/rubric?task={task}")
        except UiError as exc:
            if exc.status == 404:
                return error(request, 404, exc.message)
            return draft_view(request, task, draft_id, exc.message, status=exc.status)
        return draft_view(request, task, draft_id, "Saved." if not err else None)

    @ui.get("/rubric/drafts/{draft_id}/source")
    def rubric_draft_source(draft_id: str, task: str = "adb"):
        try:
            src = act.rubric_source(st, task, draft_id)
        except UiError:
            return PlainTextResponse("Not found", status_code=404)
        return Response(src.read_bytes(), media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="rubric-source{src.suffix}"'})

    @ui.get("/rubric/history/{name}", response_class=HTMLResponse)
    def rubric_version_page(request: Request, name: str, task: str = "adb"):
        try:
            view = act.rubric_version(st, task, name)
        except UiError as exc:
            return error(request, exc.status, exc.message)
        return page(request, "rubric_version.html", nav="rubric", **view)

    @ui.post("/rubric/history/activate")
    def rubric_history_activate(request: Request, csrf: str = Form(""), task: str = Form("adb"),
                                file: str = Form("")):
        if (denied := check_csrf(csrf)) is not None:
            return denied
        try:
            act.rubric_activate_version(st, task, file)
        except UiError as exc:
            return error(request, exc.status, exc.message)
        return go(f"/rubric?task={task}")

    @ui.get("/jobs/{job_id}/log", response_class=PlainTextResponse)
    def job_log(job_id: str):
        job = st.jobs.get(job_id) if JOB_ID_RE.match(job_id) else None
        if job is None:
            return PlainTextResponse("Job not found", status_code=404)
        return PlainTextResponse(job.log.read_text(errors="replace") if job.log.exists() else "")

    @ui.post("/jobs/{job_id}/dismiss")
    def dismiss(job_id: str, csrf: str = Form("")):
        if (denied := check_csrf(csrf)) is not None:
            return denied
        job = st.jobs.get(job_id) if JOB_ID_RE.match(job_id) else None
        if job is not None:
            job.dismissed = True
        return go("/")

    app.include_router(ui)
    from foundry.ui.api import make_router, mount_spa

    app.include_router(make_router(st))
    mount_spa(app)
    return app


async def save_upload(st: UiState, file: UploadFile) -> Path:
    """A trace upload streamed to runs/.uploads/ under the import size cap."""
    from foundry.engine.importer import MAX_FILE_BYTES
    from foundry.ui.actions import UiError

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
                raise UiError(413, f"File is over the {MAX_FILE_BYTES // (1 << 20)} MB limit.")
            out.write(chunk)
    return dest


async def read_rubric_upload(file: UploadFile | None) -> tuple[str, bytes]:
    """(file name, bytes) of an optional rubric upload, under the rubric source size cap."""
    from foundry.engine.rubrics.sources import MAX_BYTES
    from foundry.ui.actions import UiError

    if file is None or not file.filename:
        return "", b""
    chunks, size = [], 0
    while chunk := await file.read(1 << 20):
        size += len(chunk)
        if size > MAX_BYTES:
            raise UiError(413, f"The file is over the {MAX_BYTES / 1e6:.1f} MB limit.")
        chunks.append(chunk)
    return file.filename, b"".join(chunks)


def _rubric_options(st: UiState, entries: list[dict]) -> tuple[list[dict], set]:
    """Rubrics that scored runs (label, sha, count), and the shas of every task's active rubric."""
    import hashlib

    active = set()
    for task in _task_ids(st):
        f = st.root / "tasks" / task / "rubric.json"
        if _regular(f):
            active.add(hashlib.sha256(f.read_bytes()).hexdigest())
    seen: dict[str, dict] = {}
    for e in entries:
        sc = e["scores"] or {}
        sha = sc.get("rubric_sha256")
        if isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha):
            o = seen.setdefault(sha, {"sha": sha, "label": f"{sc.get('rubric_id_loaded') or sc.get('rubric_id') or '?'}"
                                                           f" v{sc.get('rubric_version') or '?'}", "runs": 0})
            o["runs"] += 1
    for o in seen.values():
        o["active"] = o["sha"] in active
    return sorted(seen.values(), key=lambda o: (not o["active"], o["label"])), active


def _rubric_info(st: UiState, run: dict, scores: dict | None) -> dict | None:
    """T1: the rubric that actually scored this run (rubric_id_loaded), linked to its history version if present."""
    if not scores:
        return None
    rid = scores.get("rubric_id_loaded") or scores.get("rubric_id")
    sha = scores.get("rubric_sha256")
    if not isinstance(rid, str):
        return None
    link = None
    task = run.get("task_id")
    if isinstance(task, str) and task in _task_ids(st) and isinstance(sha, str):
        from foundry.data.rubrics import Store

        for e in Store(st.root / "tasks" / task).history():
            if e.get("sha256") == sha:
                link = f"/rubric/history/{e['file']}?task={task}"
    return {"id": rid, "version": str(scores.get("rubric_version") or "?"), "link": link}


def _draft_from_form(form) -> dict:
    """The review form back into a rubric dict (untrusted; the store validates and canonicalises it)."""
    def num(v, cast, default):
        try:
            return cast(v)
        except (TypeError, ValueError):
            return default

    count = min(num(form.get("count"), int, 0), 50)
    crits = []
    for i in range(count):
        mx = num(form.get(f"c{i}-max"), int, 4)
        levels = {str(k): str(form.get(f"c{i}-level-{k}", "")).strip() for k in range(1, min(max(mx, 2), 20) + 1)}
        crits.append({"id": str(form.get(f"c{i}-id", "")).strip(), "name": str(form.get(f"c{i}-name", "")).strip(),
                      "description": str(form.get(f"c{i}-description", "")).strip(), "max": mx,
                      "weight": num(form.get(f"c{i}-weight"), float, 1.0),
                      "levels": {k: v for k, v in levels.items() if v}})
    return {"rubric_id": str(form.get("rubric_id", "")).strip(), "version": str(form.get("version", "")).strip(),
            "description": str(form.get("description", "")).strip(), "criteria": crits}


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

    def watch():
        code = proc.wait()
        job.finished_at = time.time()
        job.exit_code = code
        if lock_token and lock_dir is not None:
            lock.release(lock_dir, lock_token)  # whatever the exit code; only if the token still matches

    threading.Thread(target=watch, name=f"foundry-job-{job_id}", daemon=True).start()
    return job


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


def _variant_stats(rows: list[dict]) -> list[dict]:
    out = []
    for name in dict.fromkeys(r["variant"] for r in rows):
        rs = [r for r in rows if r["variant"] == name]
        ran = [r for r in rs if r.get("run_dir")]
        overall = [r["overall_f"] for r in ran if r["overall_f"] is not None]
        costs = [r["cost"] for r in ran if r["cost"] is not None]
        out.append({"variant": name, "model": rs[0].get("model"), "succeeded": sum(r["status"] == "succeeded" for r in ran),
                    "total": len(rs), "overall": spread(overall),
                    "cost": sum(costs) / len(costs) if costs else None})
    return out


COMPARE_CHOICES = 200  # runs offered in a run page's "Compare with" select


def _compare_side(path: Path) -> dict:
    """One run's numbers for the compare page (F-3), from its own files; scores are None until evaluated."""
    run = {**_read_run(path), "run_id": path.name}
    scores = _scores(path)
    if scores is not None and evaluation_interrupted(path):
        scores = None
    steps = []
    try:
        if _regular(path / "trace.jsonl"):
            steps = RunDir(path).read_steps(drop_truncated_last=True)
    except ValueError:
        pass
    tools = [s for s in steps if s.type == "tool_call"]
    costs = [s.cost_usd for s in steps if s.cost_usd is not None]
    duration = None
    try:
        fmt = "%Y-%m-%dT%H:%M:%S.%f%z"
        duration = (datetime.strptime(run["ended_at"].replace("Z", "+0000"), fmt)
                    - datetime.strptime(run["started_at"].replace("Z", "+0000"), fmt)).total_seconds()
    except (KeyError, AttributeError, TypeError, ValueError):
        pass
    markers = {s.step: s.name for s in steps if s.type == "agent_step"}
    return {"run": run, "scores": scores, "steps": steps, "cost": sum(costs) if costs else None,
            "label": dashboard.model_key(run), "agent_of": {s.step: markers[s.parent] for s in steps if s.parent in markers},
            "model_calls": sum(s.type == "llm_call" for s in steps), "tool_calls": len(tools),
            "tool_errors": sum(s.status == "error" for s in tools), "duration": duration}


def _signed(d: float, fmt: str) -> str:
    return "0" if abs(d) < 1e-9 else ("+" if d > 0 else "\u2212") + fmt.format(abs(d))


def _compare_rows(a: dict, b: dict) -> list[dict]:
    """Rows of the compare table: label, A, B, and B - A, from untrusted files (every number is type-checked)."""
    rows = []

    def add(label, va, vb, fmt="{:.2f}", indent=False, show=None, better="higher"):
        show = show or (lambda v: fmt.format(v))
        delta = _signed(vb - va, fmt) if va is not None and vb is not None else ""
        rows.append({"label": label, "a": "—" if va is None else show(va), "b": "—" if vb is None else show(vb),
                     "delta": delta, "indent": indent, "better": better})  # which way is an improvement (increment 12)

    def sc(side, *keys):
        v = side["scores"]
        for k in keys:
            v = v.get(k) if isinstance(v, dict) else None
        return _num(v)

    def criteria(side) -> dict:
        out = {}
        crit = ((side["scores"] or {}).get("output") or {}).get("criteria")
        for c in crit if isinstance(crit, list) else []:
            if isinstance(c, dict) and isinstance(c.get("id"), str) and _num(c.get("score")) is not None:
                out[c["id"]] = (_num(c["score"]), _num(c.get("max")))
        return out

    def components(side) -> dict:
        comp = ((side["scores"] or {}).get("workflow") or {}).get("components")
        return {k: _num(v) for k, v in comp.items() if isinstance(k, str)} if isinstance(comp, dict) else {}

    add("Overall", sc(a, "overall"), sc(b, "overall"))
    add("Workflow", sc(a, "workflow", "score"), sc(b, "workflow", "score"))
    ca, cb = components(a), components(b)
    for k in dict.fromkeys([*ca, *cb]):
        add(k, ca.get(k), cb.get(k), indent=True)
    add("Output", sc(a, "output", "score"), sc(b, "output", "score"))
    ka, kb = criteria(a), criteria(b)
    for k in dict.fromkeys([*ka, *kb]):
        va, vb = ka.get(k), kb.get(k)

        def frac(v):
            return f"{v[0]:g}/{v[1]:g}" if v[1] is not None else f"{v[0]:g}"
        rows.append({"label": k, "a": frac(va) if va else "—", "b": frac(vb) if vb else "—", "indent": True,
                     "delta": _signed(vb[0] - va[0], "{:g}") if va and vb else "", "better": "higher"})
    add("Cost", a["cost"], b["cost"], "${:.3f}", better="lower")
    add("Model calls", a["model_calls"], b["model_calls"], "{:g}", better=None)
    add("Tool calls (failed)", a["tool_calls"], b["tool_calls"], "{:g}", better=None)
    rows[-1]["a"] = f"{a['tool_calls']} ({a['tool_errors']})"
    rows[-1]["b"] = f"{b['tool_calls']} ({b['tool_errors']})"
    add("Duration", a["duration"], b["duration"], "{:.0f} s", better="lower")
    return rows


def _prompt_ids(st: UiState) -> list[str]:
    """Active (not archived) prompt ids of every task the configs use, read from this UI's own root."""
    ids: list[str] = []
    for stem in _configs(st):
        try:
            cfg = load_config(st.root / "config" / f"{stem}.toml")
            path = st.root / "tasks" / cfg.task.id / "prompts.jsonl"
            ids += [p.prompt_id for p in parse_prompts(path) if not p.archived and p.prompt_id not in ids]
        except (ConfigError, OSError, ValueError):  # ValueError: a file that isn't UTF-8
            continue
    return ids


def _eval_config_for(st: UiState, run: dict) -> str:
    recorded = run.get("config") or ""
    stem = Path(recorded).stem
    if recorded and CONFIG_STEM_RE.match(stem) and (st.root / "config" / f"{stem}.toml").is_file():
        return f"config/{stem}.toml"
    return "config/offline.toml"


def _short(score) -> str:
    return "—" if score is None else f"{score:.2f}".removeprefix("0")


def _when(iso: str | None) -> str:
    try:
        return datetime.strptime(iso.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z").strftime("%m-%d %H:%M")
    except (AttributeError, ValueError):
        return ""


def _cards(st: UiState) -> list[dict]:
    cards = []
    for d in _run_dirs(st):
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
                      "model": dashboard.model_key(run), "headline": headline, "sub": sub, "status": status,
                      "when": _when(started), "footer": f"{run.get('task_id', '?')} · …{d.name[-4:]}",
                      "started_at": started})
    return sorted(cards, key=lambda c: c["started_at"], reverse=True)


# --- increment 16 slice 2: the readers live in foundry.data now; these wrappers keep today's callers until slice 5 --
_regular = data_runs.is_regular
_read_json = data_runs.read_json
_read_run = data_runs.read_run
_num = data_runs.num
_float = data_runs.float_or_none
_scores = data_runs.read_scores


def _run_dirs(st: UiState) -> list[Path]:
    return data_runs.run_dirs(st.runs_dir)


def _batch_list(st: UiState) -> list[dict]:
    return data_batches.batch_list(st.batches_dir, st.runs_dir)


def _batch_rows(st: UiState, path: Path) -> list[dict]:
    return data_batches.batch_rows(path, st.runs_dir)


def _configs(st: UiState) -> list[str]:
    return data_project.config_stems(st.root)


def _task_ids(st: UiState) -> list[str]:
    return data_project.task_ids(st.root)
