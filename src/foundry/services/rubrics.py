"""Rubrics: the active one, its history, imported drafts (acquire a source, draft it with the config's judge, review,
activate, roll back), and which rubric scored which runs. Used by the API and `foundry rubric`."""

from __future__ import annotations

import hashlib
import json
import re

from foundry.config import ConfigError
from foundry.data import project
from foundry.data.rubrics import Store, StoreError, read_source
from foundry.data.tasks import RubricError, load_rubric
from foundry.engine.models import registry
from foundry.engine.models.base import ProviderError
from foundry.engine.rubrics import sources
from foundry.engine.rubrics.draft import MAX_TOKENS as DRAFT_MAX_TOKENS
from foundry.engine.rubrics.draft import draft
from foundry.engine.rubrics.sources import BINARY, SourceError
from foundry.services import cloud_state, configs, jobs, spend
from foundry.services.errors import ServiceError
from foundry.services.workspace import Workspace

SOURCE_PREVIEW = 50_000  # characters of a text source shown beside the draft


def max_source_bytes() -> int:
    """The rubric source size cap, read at call time (tests lower it)."""
    return sources.MAX_BYTES


# --- drafting (was rubrics/service.py: one entry point for the CLI and the UI) ----------------------------------


def acquire(kind: str, cfg, *, file_name: str = "", file_data: bytes = b"", text: str = "", url: str = "",
            mcp: str = "", args: str = "") -> sources.Source:
    if kind == "file":
        return sources.from_file(file_name, file_data)
    if kind == "text":
        return sources.from_text(text)
    if kind == "url":
        return sources.from_url(url)
    if kind == "mcp":
        server, _, tool = (mcp or "").partition("/")
        if not server or not tool:
            raise SourceError("Name the tool as SERVER/TOOL, e.g. roda/get_dataset_details")
        try:
            parsed = json.loads(args) if (args or "").strip() else {}
        except ValueError as exc:
            raise SourceError(f"Tool arguments are not valid JSON: {exc}") from None
        return sources.from_mcp(cfg, server, tool, parsed)
    raise SourceError(f"Unknown source kind '{kind}'")


def draft_with_model(task_id: str, source: sources.Source, cfg):
    """The model call (up to a minute): no files are touched, so it runs outside any lease."""
    # Its own output budget: a big rubric (criteria x levels + notes) must not be cut at the judge's max_tokens.
    role = cfg.judge.model_copy(update={"max_tokens": max(cfg.judge.max_tokens, DRAFT_MAX_TOKENS)})
    return draft(source, task_id, registry.build_provider(role))


def create_draft(task_dir, source: sources.Source, cfg) -> tuple[str, dict]:
    """Draft with cfg.judge and save; returns (draft_id, meta). Provider errors propagate (ProviderError)."""
    return save_draft(task_dir, source, draft_with_model(task_dir.name, source, cfg), cfg)


def save_draft(task_dir, source: sources.Source, result, cfg) -> tuple[str, dict]:
    store = Store(task_dir)
    draft_id = store.save_draft(source, result)
    price = cfg.prices.get(result.model) if result.model else None
    meta, _, _ = store.load_draft(draft_id)
    if price is not None:
        meta["usage"]["cost_usd"] = round(price.cost(result.tokens_in, result.tokens_out), 6)
        store.write_draft_meta(draft_id, meta)
    return draft_id, meta


# --- the UI's rubric pages ----------------------------------------------------------------------------------------


def store_for(ws: Workspace, task: str) -> Store:
    if task not in configs.task_ids(ws):
        raise ServiceError(404, "Unknown task")
    cloud_state.pull_project(ws, task)
    return Store(project.task_dir(ws.root, task))


def overview(ws: Workspace, task: str) -> dict:
    store = store_for(ws, task)
    cloud_state.pull_runs(ws)  # versions() counts the runs each version scored
    try:
        active, _ = load_rubric(store.task / "rubric.json")
    except RubricError:
        active = None
    versions = store.versions(ws.runs_dir)
    v = cloud_state.project_version(ws, task)
    return {"task": task, "active": active, "versions": versions, "drafts": store.drafts(),
            "active_entry": next((v for v in versions if v["active"]), None), **({} if v is None else {"base_version": v})}


def active_label(ws: Workspace, task: str = "adb") -> dict | None:
    """{id, version} of the active rubric, or None (the header's "Scoring with ...")."""
    try:
        ov = overview(ws, task)
    except ServiceError:
        return None
    return {"id": ov["active"].rubric_id, "version": ov["active"].version} if ov["active"] is not None else None


def ui_create_draft(ws: Workspace, task: str, kind: str, config: str, *, file_name: str = "", file_data: bytes = b"",
                    text: str = "", url: str = "", mcp: str = "", args: str = "") -> str:
    """Blocking (up to a minute): acquire the source and draft it. Call from a worker thread. The model call runs
    outside the task's lease; only saving the draft holds it (aws-deploy build note, slice 6b). A draft counts
    toward the spend cap (spec A5): its worst case is reserved first and settled with its recorded cost."""
    store_for(ws, task)  # 404 for an unknown task, before anything is spent
    if kind == "file" and not file_name:  # filled another field without switching the source type
        kind = "text" if text.strip() else "url" if url.strip() else "mcp" if mcp.strip() else kind
    if config not in configs.config_names(ws) or kind not in ("file", "text", "url", "mcp"):
        raise ServiceError(400, "Pick a task, a known config and a source.")
    if kind == "file" and not file_name:
        raise ServiceError(400, "Choose a file to upload.")
    try:
        cfg = configs.load(ws, config)
    except ConfigError as exc:
        raise ServiceError(400, str(exc)) from None
    reservation = spend.draft_reservation(cfg, config) if ws.cap_usd is not None else 0.0
    handle = jobs.reserve(ws, "draft", f"Rubric draft for {task}", reservation)
    cost = None
    try:
        try:
            source = acquire(kind, cfg, file_name=file_name, file_data=file_data, text=text, url=url, mcp=mcp, args=args)
            result = draft_with_model(task, source, cfg)
        except (SourceError, ConfigError) as exc:
            cost = 0.0
            raise ServiceError(400, str(exc)) from None
        except ProviderError as exc:
            raise ServiceError(502, f"The model could not draft the rubric: {exc}") from None
        with cloud_state.project_write(ws, task):
            store = store_for(ws, task)
            draft_id, meta = save_draft(store.task, source, result, cfg)
        cost = (meta.get("usage") or {}).get("cost_usd")
        return draft_id
    finally:
        jobs.settle(ws, handle, cost)


def draft_view(ws: Workspace, task: str, draft_id: str) -> dict:
    store = store_for(ws, task)
    try:
        meta, body, src = store.load_draft(draft_id)
    except StoreError as exc:
        raise ServiceError(404, str(exc)) from None
    text = None
    if src is not None and meta["source"]["format"] not in BINARY:
        text = read_source(src, SOURCE_PREVIEW).decode("utf-8", "replace")
    v = cloud_state.project_version(ws, task)
    return {"task": task, "draft_id": draft_id, "meta": meta, "body": body, "source_text": text,
            **({} if v is None else {"base_version": v})}


def update_draft(ws: Workspace, task: str, draft_id: str, body: dict, activate: bool = False) -> str | None:
    """Save an edited draft; returns its validation error (None when valid). With activate, also activates it."""
    with cloud_state.project_write(ws, task):
        store = store_for(ws, task)
        try:
            store.load_draft(draft_id)
            err = store.update_draft(draft_id, body)
            if activate:
                if err:
                    raise ServiceError(400, f"Not activated: {err}")
                store.activate_draft(draft_id)
        except StoreError as exc:
            raise ServiceError(404 if "Unknown draft" in str(exc) else 409, str(exc)) from None
        return err


def discard(ws: Workspace, task: str, draft_id: str) -> None:
    with cloud_state.project_write(ws, task):
        try:
            store_for(ws, task).discard(draft_id)
        except StoreError as exc:
            raise ServiceError(404, str(exc)) from None


def draft_source(ws: Workspace, task: str, draft_id: str) -> tuple[bytes, str]:
    """(the saved source file's bytes, its suffix) for download."""
    try:
        _, _, src = store_for(ws, task).load_draft(draft_id)
    except (StoreError, ServiceError):
        src = None
    if src is None:
        raise ServiceError(404, "Not found")
    return read_source(src), src.suffix


def version(ws: Workspace, task: str, name: str) -> dict:
    store = store_for(ws, task)
    try:
        rubric, raw = load_rubric(store.history_file(name))
    except (StoreError, RubricError):
        raise ServiceError(404, "Unknown rubric version") from None
    return {"task": task, "rubric": rubric, "active": hashlib.sha256(raw).hexdigest() == store.active_sha()}


def with_meta(store: Store, path) -> dict:
    """A rubric file in full, with its sha, whether it is active, and the history file holding the same bytes."""
    try:
        rubric, raw = load_rubric(path)
    except RubricError:
        raise ServiceError(404, "No valid rubric there") from None
    sha = hashlib.sha256(raw).hexdigest()
    file = next((e["file"] for e in store.history() if e.get("sha256") == sha), None)
    return {"rubric": rubric, "sha256": sha, "active": sha == store.active_sha(), "file": file}


def active(ws: Workspace, task: str) -> dict:
    """Increment 10, R1: the active rubric in full, with the history file holding the same bytes (if any)."""
    store = store_for(ws, task)
    return with_meta(store, store.task / "rubric.json")


def activate_version(ws: Workspace, task: str, name: str) -> None:
    with cloud_state.project_write(ws, task):
        try:
            store_for(ws, task).activate_version(name)
        except StoreError as exc:
            raise ServiceError(409 if "progress" in str(exc) else 404, str(exc)) from None


    # --- which rubric scored which runs ---------------------------------------------------------------------------------


def rubric_options(ws: Workspace, entries: list[dict]) -> tuple[list[dict], set]:
    """Rubrics that scored runs (label, sha, count), and the shas of every task's active rubric."""
    active_shas = {sha for task in configs.task_ids(ws) if (sha := project.active_rubric_sha(ws.root, task))}
    seen: dict[str, dict] = {}
    for e in entries:
        sc = e["scores"] or {}
        sha = sc.get("rubric_sha256")
        if isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha):
            o = seen.setdefault(sha, {"sha": sha, "label": f"{sc.get('rubric_id_loaded') or sc.get('rubric_id') or '?'}"
                                                           f" v{sc.get('rubric_version') or '?'}", "runs": 0})
            o["runs"] += 1
    for o in seen.values():
        o["active"] = o["sha"] in active_shas
    return sorted(seen.values(), key=lambda o: (not o["active"], o["label"])), active_shas


def rubric_info(ws: Workspace, run: dict, scores: dict | None) -> dict | None:
    """T1: the rubric that actually scored this run (rubric_id_loaded), linked to its history version if present."""
    if not scores:
        return None
    rid = scores.get("rubric_id_loaded") or scores.get("rubric_id")
    sha = scores.get("rubric_sha256")
    if not isinstance(rid, str):
        return None
    link = None
    task = run.get("task_id")
    if isinstance(task, str) and task in configs.task_ids(ws) and isinstance(sha, str):
        for e in Store(project.task_dir(ws.root, task)).history():
            if e.get("sha256") == sha:
                link = f"/rubric/history/{e['file']}?task={task}"
    return {"id": rid, "version": str(scores.get("rubric_version") or "?"), "link": link}
