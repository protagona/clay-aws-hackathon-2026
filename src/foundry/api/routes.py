"""The JSON API behind the TypeScript UI (increment 9, A1-A5; its own layer since increment 16). HTTP only: each
endpoint parses the request, calls foundry.services, and shapes the response. Nothing here reads files or scores.

Writes need the X-CSRF-Token header and, for JSON bodies, Content-Type: application/json (foundry.api.guard).
Errors are always {"error": ...} (foundry.api.errors).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from foundry.api.errors import error
from foundry.api.guard import write_check
from foundry.api.render import render
from foundry.api.uploads import read_rubric_upload, save_upload
from foundry.services import batches, configs, dashboard, imports, jobs, prompts, rubrics, runs, tools
from foundry.services.errors import ServiceError
from foundry.services.workspace import Workspace


def to_json(obj):
    """Run data (pydantic steps and rubrics, paths) as plain JSON."""
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {str(k): to_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_json(v) for v in obj]
    if isinstance(obj, Path):
        return obj.name
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def field_of(message: str | None) -> str | None:
    """'criteria.0.levels: must describe 3' -> 'criteria.0.levels' (where the form shows the message)."""
    if not message:
        return None
    msg = message.removeprefix("Not activated: ").removeprefix("Fix the draft first: ")
    head = msg.split(":", 1)[0]
    return head if head and " " not in head else None


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


def make_router(ws: Workspace, csrf: str) -> APIRouter:
    write = [Depends(write_check(csrf))]
    r = APIRouter(prefix="/api")

    # --- session & meta ---------------------------------------------------------------------------------------

    @r.get("/session")
    def session():
        return {"csrf": csrf, "configs": configs.config_names(ws), "prompts": configs.prompt_ids(ws),
                "models": configs.model_choices(ws), "tasks": configs.task_ids(ws),
                "rubric": rubrics.active_label(ws, "adb"), "aws_profile": configs.aws_profile()}

    # --- runs -------------------------------------------------------------------------------------------------

    @r.get("/runs")
    def board():
        return runs.board(ws)

    @r.get("/runs/{run_id}")
    def run_detail(run_id: str):
        view = to_json(runs.detail(ws, runs.need_run(ws, run_id)))
        report_md = view.pop("report_md")
        view["report_html"] = render(report_md) if report_md is not None else None
        for item in view["m"]["feed"]:  # model text as safe Markdown (the only HTML besides report_html)
            s = item["s"]
            s["text_html"] = render(s["text"][:20_000]) if s.get("type") == "llm_call" and s.get("text") else None
        return view

    @r.post("/runs", dependencies=write, status_code=201)
    def start_run(body: RunBody):
        return {"run_id": runs.start_run(ws, body.prompt_id, body.config)}

    @r.post("/runs/{run_id}/evaluate", dependencies=write, status_code=202)
    def evaluate(run_id: str):
        runs.start_evaluate(ws, run_id)
        return {"ok": True}

    @r.get("/compare")
    def compare(a: str = "", b: str = ""):
        sa, sb, rows = runs.compare(ws, a, b)
        return to_json({"a": sa, "b": sb, "rows": rows})

    # --- dashboard --------------------------------------------------------------------------------------------

    @r.get("/dashboard")
    def dashboard_page(rubric: str = "active"):
        return to_json(dashboard.dashboard_view(ws, rubric))

    # --- batches ----------------------------------------------------------------------------------------------

    @r.get("/batches")
    def batch_list():
        return {"batches": to_json(batches.batch_list(ws)), "running": jobs.alive_of(ws, "batch") is not None}

    @r.post("/batches", dependencies=write, status_code=201)
    def start_batch(body: BatchBody):
        return {"batch_id": batches.start_batch(ws, body.config, body.models, body.prompts, body.repeats,
                                                body.concurrency, body.evaluate, body.max_cost)}

    @r.get("/batches/{batch_id}")
    def batch(batch_id: str):
        v = batches.batch_view(ws, batch_id)
        job = v.pop("job")
        return to_json({**v, "job": None if job is None else {"id": job.id, "alive": job.alive,
                                                              "exit_code": job.exit_code}})

    # --- import & receiver ------------------------------------------------------------------------------------

    @r.post("/import", dependencies=write, status_code=201)
    async def do_import(file: UploadFile = File(...), task: str = Form("adb"), prompt_id: str = Form("imported"),
                        config: str = Form("")):
        imports.check_import_form(ws, task, prompt_id, config)
        dest = await save_upload(ws, file)
        paths = imports.import_saved_trace(ws, dest, file.filename or "", task, prompt_id, config)
        return {"runs": [p.name for p in paths]}

    @r.get("/receiver")
    def receiver():
        return imports.receiver_status(ws)

    @r.post("/receiver/start", dependencies=write)
    def receiver_start(body: ReceiverBody):
        imports.receiver_start(ws, body.task, body.prompt_id, body.config)
        return {"ok": True}

    @r.post("/receiver/stop", dependencies=write)
    def receiver_stop():
        imports.receiver_stop(ws)
        return {"ok": True}

    # --- rubric -----------------------------------------------------------------------------------------------

    @r.get("/rubric")
    def rubric(task: str = "adb"):
        return to_json(rubrics.overview(ws, task))

    @r.post("/rubric/drafts", dependencies=write, status_code=201)
    async def create_draft(task: str = Form("adb"), kind: str = Form("file"), text: str = Form(""),
                           url: str = Form(""), mcp: str = Form(""), args: str = Form(""),
                           config: str = Form("bedrock"), file: UploadFile | None = File(None)):
        name, data = await read_rubric_upload(file)
        draft_id = await run_in_threadpool(rubrics.ui_create_draft, ws, task, kind, config, file_name=name,
                                           file_data=data, text=text, url=url, mcp=mcp, args=args)
        return {"draft_id": draft_id}

    @r.get("/rubric/drafts/{draft_id}")
    def draft(draft_id: str, task: str = "adb"):
        return to_json(rubrics.draft_view(ws, task, draft_id))

    @r.put("/rubric/drafts/{draft_id}", dependencies=write)
    def update_draft(draft_id: str, body: DraftBody):
        try:
            err = rubrics.update_draft(ws, body.task, draft_id, body.body, activate=body.activate)
        except ServiceError as exc:
            if exc.status == 400:  # not activated: say which field
                return error(400, exc.message, field=field_of(exc.message))
            raise
        return {"valid": err is None, "error": err, "field": field_of(err), "activated": body.activate and err is None}

    @r.delete("/rubric/drafts/{draft_id}", dependencies=write)
    def discard(draft_id: str, task: str = "adb"):
        rubrics.discard(ws, task, draft_id)
        return {"ok": True}

    @r.get("/rubric/drafts/{draft_id}/source")
    def draft_source(draft_id: str, task: str = "adb"):
        data, suffix = rubrics.draft_source(ws, task, draft_id)
        return Response(data, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="rubric-source{suffix}"'})

    @r.get("/rubric/active")
    def rubric_active(task: str = "adb"):
        return to_json(rubrics.active(ws, task))

    @r.get("/runs/{run_id}/rubric")
    def run_rubric(run_id: str):
        return to_json(runs.run_rubric(ws, run_id))

    @r.get("/rubric/history/{name}")
    def version(name: str, task: str = "adb"):
        return to_json(rubrics.version(ws, task, name))

    @r.post("/rubric/history/activate", dependencies=write)
    def activate_version(body: HistoryBody):
        rubrics.activate_version(ws, body.task, body.file)
        return {"ok": True}

    # --- prompts (increment 13) ------------------------------------------------------------------------------

    @r.get("/prompts")
    def prompt_list(task: str = "adb"):
        return prompts.prompts_view(ws, task)

    @r.get("/prompts/tools")  # before /prompts/{prompt_id}; "tools" is a reserved prompt id
    def prompt_tools(task: str = "adb"):
        return tools.tool_menu(ws, task)

    @r.post("/prompts/tools/refresh", dependencies=write)
    def refresh_tools():
        tools.refresh_tools(ws)
        return {"ok": True}

    @r.get("/prompts/{prompt_id}")
    def prompt(prompt_id: str, task: str = "adb"):
        return prompts.prompt_view(ws, task, prompt_id)

    @r.post("/prompts", dependencies=write, status_code=201)
    def create_prompt(body: PromptBody):
        return {"prompt_id": prompts.create(ws, body.task, body.prompt_id, body.prompt)}

    @r.put("/prompts/{prompt_id}", dependencies=write)
    def update_prompt(prompt_id: str, body: PromptBody):
        prompts.set_text(ws, body.task, prompt_id, body.prompt)
        return {"ok": True}

    @r.post("/prompts/{prompt_id}/version", dependencies=write, status_code=201)
    def version_prompt(prompt_id: str, body: TaskBody):
        return {"prompt_id": prompts.new_version(ws, body.task, prompt_id)}

    @r.post("/prompts/{prompt_id}/archive", dependencies=write)
    def archive_prompt(prompt_id: str, body: ArchiveBody):
        prompts.archive(ws, body.task, prompt_id, body.archived)
        return {"ok": True}

    @r.put("/prompts/{prompt_id}/stages", dependencies=write)
    def prompt_stages(prompt_id: str, body: StagesBody):
        return {"ok": True, "expected_sha256": prompts.set_stages(ws, body.task, prompt_id, body.stages)}

    # --- jobs (toasts, failures) ------------------------------------------------------------------------------

    @r.get("/jobs")
    def job_list():
        return {"jobs": jobs.recent(ws)}

    @r.get("/jobs/{job_id}/log")
    def job_log(job_id: str):
        return {"log": jobs.log_text(ws, job_id)}

    @r.post("/jobs/{job_id}/dismiss", dependencies=write)
    def dismiss(job_id: str):
        jobs.dismiss(ws, job_id)
        return {"ok": True}

    @r.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    def unknown(rest: str):
        raise ServiceError(404, "No such API endpoint")

    return r
