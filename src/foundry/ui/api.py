"""The JSON API behind the TypeScript UI (increment 9, A1-A5). Thin: every endpoint calls foundry.ui.actions or the
same helpers the classic pages use; nothing here scores, validates rubrics or reads run files on its own.

Writes need the X-CSRF-Token header (a custom header cannot be sent cross-site without a CORS preflight, which is
never granted) and, for JSON bodies, Content-Type: application/json. Errors are always {"error": ...}.
"""

from __future__ import annotations

import hmac
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from foundry.ui import actions as act
from foundry.ui.actions import UiError

DIST = Path(__file__).with_name("web_dist")
RECENT_JOB_S = 120  # finished jobs reported to the UI for toasts


def _err(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


def _json(obj):
    """Run data (pydantic steps and rubrics, paths) as plain JSON."""
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {str(k): _json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json(v) for v in obj]
    if isinstance(obj, Path):
        return obj.name
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


# --- request bodies ---------------------------------------------------------------------------------------------


class RunBody(BaseModel):
    prompt_id: str = Field(max_length=100)
    config: str = Field(max_length=100)


class BatchBody(BaseModel):
    config: str = Field(max_length=100)
    models: list[str] = Field(default_factory=list, max_length=20)
    prompts: list[str] = Field(default_factory=list, max_length=50)
    repeats: int = 1
    concurrency: int = 2
    evaluate: bool = False
    max_cost: float = 2.0


class ReceiverBody(BaseModel):
    task: str = "adb"
    prompt_id: str = "imported"
    config: str = ""


class DraftBody(BaseModel):
    task: str = "adb"
    body: dict
    activate: bool = False


class PromptBody(BaseModel):  # increment 13: limits are checked by the store, so errors come back as 400 + field
    task: str = "adb"
    prompt_id: str | None = None
    prompt: str = ""


class TaskBody(BaseModel):
    task: str = "adb"


class ArchiveBody(BaseModel):
    task: str = "adb"
    archived: bool


class StagesBody(BaseModel):
    task: str = "adb"
    stages: list[list[str]]


class HistoryBody(BaseModel):
    task: str = "adb"
    file: str = Field(max_length=200)


def make_router(st) -> APIRouter:
    from foundry.ui import app as core

    def require_write(request: Request) -> None:
        token = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(token.encode(), st.csrf.encode()):
            raise UiError(403, "Missing or invalid CSRF token; reload the page")
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        multipart = request.url.path in ("/api/import", "/api/rubric/drafts")
        if request.headers.get("content-length", "0") not in ("", "0") or ctype:
            allowed = ("multipart/form-data", "application/x-www-form-urlencoded") if multipart else ("application/json",)
            if ctype not in allowed:  # the CSRF header above is what stops cross-site writes; this keeps bodies typed
                raise UiError(415, "multipart/form-data required" if multipart else "Content-Type must be application/json")

    write = [Depends(require_write)]
    r = APIRouter(prefix="/api")

    # --- session & meta ---------------------------------------------------------------------------------------

    @r.get("/session")
    def session():
        rubric = None
        try:
            ov = act.rubric_overview(st, "adb")
            if ov["active"] is not None:
                rubric = {"id": ov["active"].rubric_id, "version": ov["active"].version}
        except UiError:
            pass
        return {"csrf": st.csrf, "configs": core._configs(st), "prompts": core._prompt_ids(st),
                "models": core._model_choices(st), "tasks": core._task_ids(st), "rubric": rubric,
                "aws_profile": os.environ.get("AWS_PROFILE") or None}

    # --- runs -------------------------------------------------------------------------------------------------

    @r.get("/runs")
    def runs():
        cards = core._cards(st)
        k = act.board_kpis(st)
        out = []
        for c in cards:
            if c["unreadable"]:
                out.append({"run_id": c["name"], "unreadable": True, "column": "attention", "prompt_id": "?",
                            "model": "unreadable run.json", "status": "?", "headline": "unreadable",
                            "kpi": {"spent": 0.0, "overall": None}})
                continue
            out.append({**c, "column": act.board_column(c), **_card_extras(st, c["run_id"]),
                        "kpi": k["shares"].get(c["run_id"], {"spent": 0.0, "overall": None})})
        live = sum(c["column"] in ("running", "evaluating") for c in out)
        today = time.strftime("%Y-%m-%d", time.gmtime())
        return {"runs": out, "live": live + (1 if st.receiver is not None else 0),
                "kpis": {"runs": len(out), "today": sum(str(c.get("started_at", "")).startswith(today) for c in out),
                         "mean": k["mean"], "scored": k["scored"], "spent": k["spent"],
                         "live": live, "running": sum(c["column"] == "running" for c in out),
                         "evaluating": sum(c["column"] == "evaluating" for c in out)}}

    @r.get("/runs/{run_id}")
    def run_detail(run_id: str):
        from foundry.ui.render import render

        path = act.need_run(st, run_id)
        view = _json(act.run_view(st, path))
        for item in view["m"]["feed"]:  # model text as safe Markdown (the only HTML besides report_html)
            s = item["s"]
            s["text_html"] = render(s["text"][:20_000]) if s.get("type") == "llm_call" and s.get("text") else None
        return view

    @r.post("/runs", dependencies=write, status_code=201)
    def start_run(body: RunBody):
        return {"run_id": act.start_run(st, body.prompt_id, body.config)}

    @r.post("/runs/{run_id}/evaluate", dependencies=write, status_code=202)
    def evaluate(run_id: str):
        act.start_evaluate(st, run_id)
        return {"ok": True}

    @r.get("/compare")
    def compare(a: str = "", b: str = ""):
        sa, sb, rows = act.compare(st, a, b)
        return _json({"a": sa, "b": sb, "rows": rows})

    # --- dashboard --------------------------------------------------------------------------------------------

    @r.get("/dashboard")
    def dashboard(rubric: str = "active"):
        return _json(act.dashboard_view(st, rubric))

    # --- batches ----------------------------------------------------------------------------------------------

    @r.get("/batches")
    def batches():
        return {"batches": _json(core._batch_list(st)),
                "running": any(j.kind == "batch" and j.alive for j in st.jobs.values())}

    @r.post("/batches", dependencies=write, status_code=201)
    def start_batch(body: BatchBody):
        return {"batch_id": act.start_batch(st, body.config, body.models, body.prompts, body.repeats,
                                            body.concurrency, body.evaluate, body.max_cost)}

    @r.get("/batches/{batch_id}")
    def batch(batch_id: str):
        v = act.batch_view(st, batch_id)
        job = v.pop("job")
        return _json({**v, "job": None if job is None else {"id": job.id, "alive": job.alive,
                                                            "exit_code": job.exit_code}})

    # --- import & receiver ------------------------------------------------------------------------------------

    @r.post("/import", dependencies=write, status_code=201)
    async def do_import(file: UploadFile = File(...), task: str = Form("adb"), prompt_id: str = Form("imported"),
                        config: str = Form("")):
        act.check_import_form(st, task, prompt_id, config)
        dest = await core.save_upload(st, file)
        paths = act.import_saved_trace(st, dest, file.filename or "", task, prompt_id, config)
        return {"runs": [p.name for p in paths]}

    @r.get("/receiver")
    def receiver():
        rec = st.receiver
        return {"running": rec is not None, "port": st.receiver_port,
                "endpoint": f"http://127.0.0.1:{st.receiver_port}",
                "count": len(getattr(rec, "written", [])),
                "received": [p.name for p in getattr(rec, "written", [])][-20:]}

    @r.post("/receiver/start", dependencies=write)
    def receiver_start(body: ReceiverBody):
        act.receiver_start(st, body.task, body.prompt_id, body.config)
        return {"ok": True}

    @r.post("/receiver/stop", dependencies=write)
    def receiver_stop():
        act.receiver_stop(st)
        return {"ok": True}

    # --- rubric -----------------------------------------------------------------------------------------------

    @r.get("/rubric")
    def rubric(task: str = "adb"):
        return _json(act.rubric_overview(st, task))

    @r.post("/rubric/drafts", dependencies=write, status_code=201)
    async def create_draft(task: str = Form("adb"), kind: str = Form("file"), text: str = Form(""),
                           url: str = Form(""), mcp: str = Form(""), args: str = Form(""),
                           config: str = Form("bedrock"), file: UploadFile | None = File(None)):
        from starlette.concurrency import run_in_threadpool

        name, data = await core.read_rubric_upload(file)
        draft_id = await run_in_threadpool(act.rubric_create_draft, st, task, kind, config, file_name=name,
                                           file_data=data, text=text, url=url, mcp=mcp, args=args)
        return {"draft_id": draft_id}

    @r.get("/rubric/drafts/{draft_id}")
    def draft(draft_id: str, task: str = "adb"):
        return _json(act.rubric_draft(st, task, draft_id))

    @r.put("/rubric/drafts/{draft_id}", dependencies=write)
    def update_draft(draft_id: str, body: DraftBody):
        try:
            err = act.rubric_update_draft(st, body.task, draft_id, body.body, activate=body.activate)
        except UiError as exc:
            if exc.status == 400:  # not activated: say which field
                return _err(400, exc.message, field=_field(exc.message))
            raise
        return {"valid": err is None, "error": err, "field": _field(err), "activated": body.activate and err is None}

    @r.delete("/rubric/drafts/{draft_id}", dependencies=write)
    def discard(draft_id: str, task: str = "adb"):
        act.rubric_discard(st, task, draft_id)
        return {"ok": True}

    @r.get("/rubric/drafts/{draft_id}/source")
    def draft_source(draft_id: str, task: str = "adb"):
        src = act.rubric_source(st, task, draft_id)
        return Response(src.read_bytes(), media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="rubric-source{src.suffix}"'})

    @r.get("/rubric/active")
    def rubric_active(task: str = "adb"):
        return _json(act.rubric_active(st, task))

    @r.get("/runs/{run_id}/rubric")
    def run_rubric(run_id: str):
        return _json(act.run_rubric(st, run_id))

    @r.get("/rubric/history/{name}")
    def version(name: str, task: str = "adb"):
        return _json(act.rubric_version(st, task, name))

    # --- prompts (increment 13) ------------------------------------------------------------------------------

    @r.get("/prompts")
    def prompts(task: str = "adb"):
        return act.prompts_view(st, task)

    @r.get("/prompts/tools")  # before /prompts/{prompt_id}; "tools" is a reserved prompt id
    def prompt_tools(task: str = "adb"):
        return {"tools": act.tool_suggestions(st, task)}

    @r.get("/prompts/{prompt_id}")
    def prompt(prompt_id: str, task: str = "adb"):
        return act.prompt_view(st, task, prompt_id)

    @r.post("/prompts", dependencies=write, status_code=201)
    def create_prompt(body: PromptBody):
        return {"prompt_id": act.prompt_create(st, body.task, body.prompt_id, body.prompt)}

    @r.put("/prompts/{prompt_id}", dependencies=write)
    def update_prompt(prompt_id: str, body: PromptBody):
        act.prompt_set_text(st, body.task, prompt_id, body.prompt)
        return {"ok": True}

    @r.post("/prompts/{prompt_id}/version", dependencies=write, status_code=201)
    def version_prompt(prompt_id: str, body: TaskBody):
        return {"prompt_id": act.prompt_new_version(st, body.task, prompt_id)}

    @r.post("/prompts/{prompt_id}/archive", dependencies=write)
    def archive_prompt(prompt_id: str, body: ArchiveBody):
        act.prompt_archive(st, body.task, prompt_id, body.archived)
        return {"ok": True}

    @r.put("/prompts/{prompt_id}/stages", dependencies=write)
    def prompt_stages(prompt_id: str, body: StagesBody):
        sha = act.prompt_set_stages(st, body.task, prompt_id, body.stages)
        return {"ok": True, "expected_sha256": sha}

    @r.post("/rubric/history/activate", dependencies=write)
    def activate_version(body: HistoryBody):
        act.rubric_activate_version(st, body.task, body.file)
        return {"ok": True}

    # --- jobs (toasts, failures) ------------------------------------------------------------------------------

    @r.get("/jobs")
    def jobs():
        now = time.time()
        out = []
        for j in st.jobs.values():
            finished = getattr(j, "finished_at", None)
            if j.alive or (finished and now - finished <= RECENT_JOB_S) or j.failed_visibly:
                out.append({"id": j.id, "kind": j.kind, "run_id": j.run_id, "label": j.label, "alive": j.alive,
                            "exit_code": j.exit_code, "failed": j.failed_visibly, "finished_at": finished,
                            "message": j.last_stderr_line() if j.failed_visibly else None})
        return {"jobs": out}

    @r.get("/jobs/{job_id}/log")
    def job_log(job_id: str):
        job = st.jobs.get(job_id) if core.JOB_ID_RE.match(job_id) else None
        if job is None:
            raise UiError(404, "Job not found")
        return {"log": job.log.read_text(errors="replace") if job.log.exists() else ""}

    @r.post("/jobs/{job_id}/dismiss", dependencies=write)
    def dismiss(job_id: str):
        job = st.jobs.get(job_id) if core.JOB_ID_RE.match(job_id) else None
        if job is not None:
            job.dismissed = True
        return {"ok": True}

    @r.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    def unknown(rest: str):
        raise UiError(404, "No such API endpoint")

    return r


def _field(message: str | None) -> str | None:
    """'criteria.0.levels: must describe 3' -> 'criteria.0.levels' (where the form shows the message)."""
    if not message:
        return None
    msg = message.removeprefix("Not activated: ").removeprefix("Fix the draft first: ")
    head = msg.split(":", 1)[0]
    return head if head and " " not in head else None


def _card_extras(st, run_id: str) -> dict:
    """Numbers a board card shows that the classic card didn't need (all from the run's own files)."""
    from foundry.ui import app as core

    path = st.runs_dir / run_id
    sc = core._scores(path) or {}
    run = core._read_run(path) or {}
    agents = run.get("agents") if isinstance(run.get("agents"), dict) else {}
    cost = None
    wf = sc.get("workflow") if isinstance(sc.get("workflow"), dict) else {}
    if isinstance(wf.get("cost_usd"), (int, float)):
        cost = wf["cost_usd"]
    mode = agents.get("mode") if isinstance(agents.get("mode"), str) else None
    started: list[str] = []
    live_pipeline = mode == "pipeline" and run.get("status") == "running"
    if (live_pipeline or cost is None) and core._regular(path / "trace.jsonl"):
        import json as _j  # unscored runs: cost from the trace; live pipeline runs: agents started so far

        total, seen_cost = 0.0, False
        for line in (path / "trace.jsonl").read_text(errors="replace").splitlines():
            try:
                s = _j.loads(line)
            except ValueError:
                continue
            if not isinstance(s, dict):
                continue
            if live_pipeline and s.get("type") == "agent_step" and isinstance(s.get("name"), str):
                started.append(s["name"])
            c = s.get("cost_usd")
            if isinstance(c, (int, float)) and not isinstance(c, bool):
                total, seen_cost = total + c, True
        if cost is None and seen_cost:
            cost = round(total, 6)
    return {"overall": sc.get("overall"), "workflow": wf.get("score"),
            "output": (sc.get("output") or {}).get("score") if isinstance(sc.get("output"), dict) else None,
            "cost": cost, "agents_mode": mode, "agents_started": started[:8]}


def install(app: FastAPI) -> None:
    """Error handlers: /api problems are always JSON {"error"} (A4)."""

    @app.exception_handler(UiError)
    async def ui_error(request: Request, exc: UiError):
        return _err(exc.status, exc.message, **({"field": exc.field} if exc.field else {}))

    @app.exception_handler(RequestValidationError)
    async def bad_body(request: Request, exc: RequestValidationError):
        first = (exc.errors() or [{}])[0]
        where = ".".join(str(p) for p in first.get("loc", []) if p != "body") or "request"
        return _err(422, f"{where}: {first.get('msg', 'invalid')}")


def mount_spa(app: FastAPI) -> None:
    """The built TypeScript app: /assets/* as files, every other non-API path falls back to index.html."""
    install(app)

    @app.get("/assets/{name:path}")
    def assets(name: str):
        f = (DIST / "assets" / name).resolve()
        if not f.is_relative_to((DIST / "assets").resolve()) or not f.is_file():
            return _err(404, "Not found")
        return FileResponse(f, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/{rest:path}", response_class=HTMLResponse)
    def spa(rest: str):
        if rest == "classic":
            from fastapi.responses import RedirectResponse

            return RedirectResponse("/classic/", status_code=307)
        index = DIST / "index.html"
        if not index.is_file():
            return HTMLResponse('<!doctype html><title>Foundry</title><p>UI bundle not built; run '
                                '<code>npm --prefix web run build</code>, or use <a href="/classic/">the classic UI</a>.',
                                status_code=503)
        return HTMLResponse(index.read_text(), headers={"Cache-Control": "no-cache"})
