"""The project's configs and tasks as the app offers them: config names, task ids, batch models, active prompt ids,
and whether a prompt can run on a config. A leaf: it imports no other services module except errors and workspace."""

from __future__ import annotations

import os
from pathlib import Path

from foundry.config import Config, ConfigError, load_config
from foundry.data import project
from foundry.data.tasks import parse_prompts
from foundry.services import state
from foundry.services.errors import ServiceError
from foundry.services.workspace import Workspace


def config_names(ws: Workspace) -> list[str]:
    return project.config_stems(ws.root)


def task_ids(ws: Workspace) -> list[str]:
    state.of(ws).fresh_projects(ws)
    return project.task_ids(ws.root)


def load(ws: Workspace, stem: str) -> Config:
    return load_config(project.config_path(ws.root, stem))


def model_choices(ws: Workspace) -> list[str]:
    """Models offered for batches: every agent model and priced model in the configs (an allowlist)."""
    out: set[str] = set()
    for stem in config_names(ws):
        try:
            cfg = load(ws, stem)
        except ConfigError:
            continue
        if cfg.model.provider == "bedrock":
            out.add(cfg.model.id)
            out.update(cfg.prices)
    return sorted(out)


def prompt_ids(ws: Workspace) -> list[str]:
    """Active (not archived) prompt ids of every task the configs use, read from this UI's own root."""
    ids: list[str] = []
    state.of(ws).fresh_projects(ws)
    for stem in config_names(ws):
        try:
            cfg = load(ws, stem)
            path = project.task_dir(ws.root, cfg.task.id) / "prompts.jsonl"
            ids += [p.prompt_id for p in parse_prompts(path) if not p.archived and p.prompt_id not in ids]
        except (ConfigError, OSError, ValueError):  # ValueError: a file that isn't UTF-8
            continue
    return ids


def eval_config_for(ws: Workspace, run: dict) -> str:
    recorded = run.get("config") or ""
    stem = Path(recorded).stem
    if recorded and project.config_exists(ws.root, stem):
        return f"config/{stem}.toml"
    return "config/offline.toml"


def relative_paths(ws: Workspace, msg: str) -> str:
    """Error text without the server's absolute paths."""
    return msg.replace(f"{ws.root}/", "").replace(str(ws.root), ".")


def task_prompts(ws: Workspace, task: str) -> list:
    """Every prompt of a task in this UI's own root (archived ones included)."""
    state.of(ws).fresh_project(ws, task)
    try:
        return project.task_prompts(ws.root, task)
    except ConfigError as exc:
        if "no prompts for task" in str(exc):
            raise
        raise ServiceError(400, relative_paths(ws, str(exc))) from None


def check_runnable(ws: Workspace, cfg, config: str, ids: list[str], prompts: list) -> None:
    """Known and not archived; a config that replays scripts must have one for each prompt (P7, P10)."""
    by_id = {p.prompt_id: p for p in prompts}
    for pid in ids:
        p = by_id.get(pid)
        if p is None:
            active = ", ".join(q.prompt_id for q in prompts if not q.archived)
            raise ServiceError(400, f"Unknown prompt id '{pid}' for task '{cfg.task.id}'. Available: {active}")
        if p.archived:
            raise ServiceError(400, f"{pid} is archived. Unarchive it on the Prompts page to run it.")
        if cfg.model.provider == "scripted" and cfg.model.script:
            if not project.script_exists(ws.root, cfg.model.script.format(prompt_id=pid)):
                raise ServiceError(400, f"The offline config '{config}' has no script for {pid}. Pick a Bedrock config.")


def require_config(ws: Workspace, config: str) -> None:
    names = config_names(ws)
    if config not in names:
        raise ServiceError(400, f"Unknown config '{config}'. Available: {', '.join(names)}")


def aws_profile() -> str | None:
    return os.environ.get("AWS_PROFILE") or None
