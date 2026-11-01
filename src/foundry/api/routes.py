"""The JSON API behind the TypeScript UI (increment 9, A1-A5; its own layer since increment 16). HTTP only: each
endpoint parses the request, calls foundry.services, and shapes the response. Nothing here reads files or scores.

Every route declares its request and response models (foundry.api.schemas, increment 17); FastAPI checks each
response against its model, and keys a service leaves out stay out (exclude_unset). Writes need the X-CSRF-Token
header and, for JSON bodies, Content-Type: application/json (foundry.api.guard). Errors are always {"error": ...}
(foundry.api.errors).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from foundry.api.errors import error
from foundry.api.guard import expected_csrf, write_check
from foundry.api import schemas as S
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


def make_router(ws: Workspace, csrf: str, access=None) -> APIRouter:
    write = [Depends(write_check(csrf, access))]
    r = APIRouter(prefix="/api")

    def route(method: str, path: str, model, **kw):
        """Every JSON route: its response model, and absent keys stay absent (never become null)."""
        return r.api_route(path, methods=[method], response_model=model, response_model_exclude_unset=True, **kw)

    # --- session & meta ---------------------------------------------------------------------------------------

    @route("GET", "/session", S.Session)
    def session(request: Request):
        out = {"csrf": expected_csrf(request, csrf, access) or "", "configs": configs.config_names(ws),
               "prompts": configs.prompt_ids(ws), "models": configs.model_choices(ws), "tasks": configs.task_ids(ws),
               "rubric": rubrics.active_label(ws, "adb"), "aws_profile": configs.aws_profile()}
        if access is not None:
            out["user"] = request.state.auth[1]
        return out

    # --- sign-in (only when the deployment has auth on: aws-deploy ADR-9) --------------------------------------

    if access is not None:
        from foundry.services.access import COOKIE, SESSION_S

        def set_cookie(response: Response, value: str, max_age: int) -> None:
            response.set_cookie(COOKIE, value, max_age=max_age, path="/", secure=True, httponly=True,
                                samesite="strict")

        @route("POST", "/auth/login", S.AuthMe, dependencies=[Depends(write_check(csrf, access, check_csrf=False))])
        def login(body: S.LoginBody, request: Request, response: Response):
            access.limit(request.client.host if request.client else "")  # every attempt counts
            name = access.token_name(body.token)
            if name is None:
                raise ServiceError(401, "That token isn't valid.")
            set_cookie(response, access.new_session(name), SESSION_S)
            return {"name": name}

        @route("POST", "/auth/logout", S.Ok, dependencies=write)
        def logout(response: Response):
            set_cookie(response, "", 0)
            return {"ok": True}

        @route("GET", "/auth/me", S.AuthMe)
        def me(request: Request):
            return {"name": request.state.auth[1]}

    # --- runs -------------------------------------------------------------------------------------------------

    @route("GET", "/runs", S.RunsResponse)
    def board():
        return runs.board(ws)

    @route("GET", "/runs/{run_id}", S.RunDetail)
    def run_detail(run_id: str):
        view = to_json(runs.detail(ws, runs.need_run(ws, run_id)))
        report_md = view.pop("report_md")
        view["report_html"] = render(report_md) if report_md is not None else None
        for item in view["m"]["feed"]:  # model text as safe Markdown (the only HTML besides report_html)
            s = item["s"]
            s["text_html"] = render(s["text"][:20_000]) if s.get("type") == "llm_call" and s.get("text") else None
        return view

    @route("POST", "/runs", S.StartRunResponse, dependencies=write, status_code=201)
    def start_run(body: S.RunBody):
        return {"run_id": runs.start_run(ws, body.prompt_id, body.config)}

    @route("POST", "/runs/{run_id}/evaluate", S.Ok, dependencies=write, status_code=202)
    def evaluate(run_id: str):
        runs.start_evaluate(ws, run_id)
        return {"ok": True}

    @route("GET", "/compare", S.Compare)
    def compare(a: str = "", b: str = ""):
        sa, sb, rows = runs.compare(ws, a, b)
        return to_json({"a": sa, "b": sb, "rows": rows})

    # --- dashboard --------------------------------------------------------------------------------------------

    @route("GET", "/dashboard", S.Dashboard)
    def dashboard_page(rubric: str = "active"):
        return to_json(dashboard.dashboard_view(ws, rubric))

    # --- batches ----------------------------------------------------------------------------------------------

    @route("GET", "/batches", S.BatchesResponse)
    def batch_list():
        return {"batches": to_json(batches.batch_list(ws)), "running": bool(jobs.live(ws, "batch"))}

    @route("POST", "/batches", S.StartBatchResponse, dependencies=write, status_code=201)
    def start_batch(body: S.BatchBody):
        return {"batch_id": batches.start_batch(ws, body.config, body.models, body.prompts, body.repeats,
                                                body.concurrency, body.evaluate, body.max_cost)}

    @route("GET", "/batches/{batch_id}", S.BatchDetail)
    def batch(batch_id: str):
        v = batches.batch_view(ws, batch_id)
        job = v.pop("job")
        return to_json({**v, "job": None if job is None else {"id": job.id, "alive": job.alive,
                                                              "exit_code": job.exit_code}})

    # --- import & receiver ------------------------------------------------------------------------------------

    @route("POST", "/import", S.ImportResponse, dependencies=write, status_code=201)
    async def do_import(file: UploadFile = File(...), task: str = Form("adb"), prompt_id: str = Form("imported"),
                        config: str = Form("")):
        imports.check_import_form(ws, task, prompt_id, config)
        dest = await save_upload(ws, file)
        paths = imports.import_saved_trace(ws, dest, file.filename or "", task, prompt_id, config)
        return {"runs": [p.name for p in paths]}

    @route("GET", "/receiver", S.ReceiverStatus)
    def receiver():
        return imports.receiver_status(ws)

    @route("POST", "/receiver/start", S.Ok, dependencies=write)
    def receiver_start(body: S.ReceiverBody):
        imports.receiver_start(ws, body.task, body.prompt_id, body.config)
        return {"ok": True}

    @route("POST", "/receiver/stop", S.Ok, dependencies=write)
    def receiver_stop():
        imports.receiver_stop(ws)
        return {"ok": True}

    # --- rubric -----------------------------------------------------------------------------------------------

    @route("GET", "/rubric", S.RubricOverview)
    def rubric(task: str = "adb"):
        return to_json(rubrics.overview(ws, task))

    @route("POST", "/rubric/drafts", S.CreateDraftResponse, dependencies=write, status_code=201)
    async def create_draft(task: str = Form("adb"), kind: str = Form("file"), text: str = Form(""),
                           url: str = Form(""), mcp: str = Form(""), args: str = Form(""),
                           config: str = Form("bedrock"), file: UploadFile | None = File(None)):
        name, data = await read_rubric_upload(file)
        draft_id = await run_in_threadpool(rubrics.ui_create_draft, ws, task, kind, config, file_name=name,
                                           file_data=data, text=text, url=url, mcp=mcp, args=args)
        return {"draft_id": draft_id}

    @route("GET", "/rubric/drafts/{draft_id}", S.DraftView)
    def draft(draft_id: str, task: str = "adb"):
        return to_json(rubrics.draft_view(ws, task, draft_id))

    @route("PUT", "/rubric/drafts/{draft_id}", S.DraftSaveResult, dependencies=write)
    def update_draft(draft_id: str, body: S.DraftBody):
        try:
            err = rubrics.update_draft(ws, body.task, draft_id, body.body, activate=body.activate)
        except ServiceError as exc:
            if exc.status == 400:  # not activated: say which field
                return error(400, exc.message, field=field_of(exc.message))
            raise
        return {"valid": err is None, "error": err, "field": field_of(err), "activated": body.activate and err is None}

    @route("DELETE", "/rubric/drafts/{draft_id}", S.Ok, dependencies=write)
    def discard(draft_id: str, task: str = "adb"):
        rubrics.discard(ws, task, draft_id)
        return {"ok": True}

    @r.get("/rubric/drafts/{draft_id}/source", response_class=Response)  # a file download, not JSON (exempt)
    def draft_source(draft_id: str, task: str = "adb"):
        data, suffix = rubrics.draft_source(ws, task, draft_id)
        return Response(data, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="rubric-source{suffix}"'})

    @route("GET", "/rubric/active", S.RubricWithMeta)
    def rubric_active(task: str = "adb"):
        return to_json(rubrics.active(ws, task))

    @route("GET", "/runs/{run_id}/rubric", S.RubricWithMeta)
    def run_rubric(run_id: str):
        return to_json(runs.run_rubric(ws, run_id))

    @route("GET", "/rubric/history/{name}", S.RubricVersion)
    def version(name: str, task: str = "adb"):
        return to_json(rubrics.version(ws, task, name))

    @route("POST", "/rubric/history/activate", S.Ok, dependencies=write)
    def activate_version(body: S.HistoryBody):
        rubrics.activate_version(ws, body.task, body.file)
        return {"ok": True}

    # --- prompts (increment 13) ------------------------------------------------------------------------------

    @route("GET", "/prompts", S.PromptsResponse)
    def prompt_list(task: str = "adb"):
        return prompts.prompts_view(ws, task)

    @route("GET", "/prompts/tools", S.ToolMenu)  # before /prompts/{prompt_id}; "tools" is a reserved prompt id
    def prompt_tools(task: str = "adb"):
        return tools.tool_menu(ws, task)

    @route("POST", "/prompts/tools/refresh", S.Ok, dependencies=write)
    def refresh_tools():
        tools.refresh_tools(ws)
        return {"ok": True}

    @route("GET", "/prompts/{prompt_id}", S.PromptDetail)
    def prompt(prompt_id: str, task: str = "adb"):
        return prompts.prompt_view(ws, task, prompt_id)

    @route("POST", "/prompts", S.CreatePromptResponse, dependencies=write, status_code=201)
    def create_prompt(body: S.PromptBody):
        return {"prompt_id": prompts.create(ws, body.task, body.prompt_id, body.prompt)}

    @route("PUT", "/prompts/{prompt_id}", S.Ok, dependencies=write)
    def update_prompt(prompt_id: str, body: S.PromptBody):
        prompts.set_text(ws, body.task, prompt_id, body.prompt)
        return {"ok": True}

    @route("POST", "/prompts/{prompt_id}/version", S.CreatePromptResponse, dependencies=write, status_code=201)
    def version_prompt(prompt_id: str, body: S.TaskBody):
        return {"prompt_id": prompts.new_version(ws, body.task, prompt_id)}

    @route("POST", "/prompts/{prompt_id}/archive", S.Ok, dependencies=write)
    def archive_prompt(prompt_id: str, body: S.ArchiveBody):
        prompts.archive(ws, body.task, prompt_id, body.archived)
        return {"ok": True}

    @route("PUT", "/prompts/{prompt_id}/stages", S.StagesSaved, dependencies=write)
    def prompt_stages(prompt_id: str, body: S.StagesBody):
        return {"ok": True, "expected_sha256": prompts.set_stages(ws, body.task, prompt_id, body.stages)}

    # --- jobs (toasts, failures) ------------------------------------------------------------------------------

    @route("GET", "/jobs", S.JobsResponse)
    def job_list():
        return {"jobs": jobs.recent(ws)}

    @route("GET", "/jobs/{job_id}/log", S.JobLog)
    def job_log(job_id: str):
        return {"log": jobs.log_text(ws, job_id)}

    @route("POST", "/jobs/{job_id}/dismiss", S.Ok, dependencies=write)
    def dismiss(job_id: str):
        jobs.dismiss(ws, job_id)
        return {"ok": True}

    @r.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], responses={404: {}})  # only ever 404
    def unknown(rest: str):
        raise ServiceError(404, "No such API endpoint")

    return r
