"""Prompts (increment 13): the cards with their run counts and scores, one prompt's page, and the edits a prompt
allows (text only while it has no runs, a new version, archiving, expected steps)."""

from __future__ import annotations

from foundry.config import ConfigError
from foundry.data import project
from foundry.data import runs as data_runs
from foundry.data.prompts import PromptStore, StoreError
from foundry.data.tasks import PROMPT_ID_RE, RubricError
from foundry.services import configs, runs, stats
from foundry.services.errors import ServiceError
from foundry.services.workspace import Workspace


def prompt_store(ws: Workspace, task: str) -> PromptStore:
    if task not in configs.task_ids(ws):
        raise ServiceError(400, f"Unknown task '{task}'")
    return PromptStore(project.task_dir(ws.root, task), task)


def call(ws: Workspace, fn, *args):
    """A store call, with its errors as ServiceError (paths relative to the project root)."""
    try:
        return fn(*args)
    except StoreError as exc:
        raise ServiceError(exc.status, configs.relative_paths(ws, exc.message), exc.field) from None
    except (ConfigError, RubricError) as exc:
        raise ServiceError(400, configs.relative_paths(ws, str(exc))) from None


def need_prompt_id(pid: str) -> None:
    if not PROMPT_ID_RE.match(pid) or ".." in pid:
        raise ServiceError(404, "No such prompt")


def runs_by_prompt(ws: Workspace, task: str) -> tuple[dict[str, list[dict]], list[str]]:
    """Readable runs of the task grouped by prompt id, and the names of unreadable run dirs (counted by name)."""
    by: dict[str, list[dict]] = {}
    unreadable = []
    for d in data_runs.run_dirs(ws.runs_dir):
        run = data_runs.read_run(d)
        if run is None:
            unreadable.append(d.name)
        elif run.get("task_id") == task and isinstance(run.get("prompt_id"), str):
            by.setdefault(run["prompt_id"], []).append({"run_id": d.name, "run": run})
    return by, unreadable


def pending(ws: Workspace, pid: str) -> bool:
    """A live UI run of this prompt, or a live batch that includes it (read without ws.lock: callers may hold it)."""
    for j in list(ws.jobs.values()):
        if not j.alive:
            continue
        if j.kind == "run" and f"_{pid}_" in j.run_id:
            return True
        if j.kind == "batch" and pid in (j.prompts or []):
            return True
    return False


def run_count(ws: Workspace, task: str, pid: str, index=None) -> dict:
    by, unreadable = index or runs_by_prompt(ws, task)
    n = len(by.get(pid, [])) + sum(f"_{pid}_" in name for name in unreadable)
    return {"runs": n, "pending": pending(ws, pid)}


def prompts_view(ws: Workspace, task: str) -> dict:
    store = prompt_store(ws, task)
    rows = call(ws, store.rows)
    index = runs_by_prompt(ws, task)
    shares = runs.board_kpis(ws)["shares"]
    out = []
    for r in rows:
        own = [shares.get(x["run_id"], {}).get("overall") for x in index[0].get(r["prompt_id"], [])]
        scored = [v for v in own if isinstance(v, (int, float))]
        out.append({**r, **run_count(ws, task, r["prompt_id"], index), "scored": len(scored),
                    "mean": sum(scored) / len(scored) if scored else None})
    return {"prompts": out, "next_id": call(ws, store.next_id, set(index[0])), "root_name": ws.root.name}


def prompt_view(ws: Workspace, task: str, pid: str) -> dict:
    need_prompt_id(pid)
    store = prompt_store(ws, task)
    row = call(ws, store.get, pid)
    if row is None:
        raise ServiceError(404, f"No prompt {pid}")
    index = runs_by_prompt(ws, task)
    count = run_count(ws, task, pid, index)
    shares = runs.board_kpis(ws)["shares"]
    mine = index[0].get(pid, [])
    rows = sorted(({"run_id": x["run_id"], "model": stats.model_key(x["run"]), "started_at": x["run"].get("started_at"),
                    "overall": runs.overall(ws, x["run_id"]), "cost": shares.get(x["run_id"], {}).get("spent")}
                   for x in mine), key=lambda r: str(r["started_at"] or ""), reverse=True)
    scored = [v for v in (shares.get(x["run_id"], {}).get("overall") for x in mine) if isinstance(v, (int, float))]
    return {"prompt": row, "runs_total": count["runs"], "pending": count["pending"],
            "next_version_id": call(ws, store.next_version_id, pid, set(index[0])),
            "locked": count["runs"] > 0 or count["pending"], "scored": len(scored),
            "mean": sum(scored) / len(scored) if scored else None, "runs": rows[:20]}


def create(ws: Workspace, task: str, pid, text) -> str:
    store = prompt_store(ws, task)
    index = runs_by_prompt(ws, task)
    taken = set(index[0]) | ({pid} if isinstance(pid, str) and run_count(ws, task, pid, index)["runs"] else set())
    call(ws, store.create, pid, text, taken)
    return pid


def set_text(ws: Workspace, task: str, pid: str, text) -> None:
    need_prompt_id(pid)
    store = prompt_store(ws, task)
    with ws.lock:  # no run may start between the check and the write
        count = run_count(ws, task, pid)
        if count["runs"]:
            raise ServiceError(409, f"{pid} has {count['runs']} run{'s' if count['runs'] != 1 else ''}, so its text "
                                    "can't change. Make a new version instead.")
        if count["pending"]:
            raise ServiceError(409, f"{pid} has a run in progress, so its text can't change. "
                                    "Try again when it finishes, or make a new version.")
        call(ws, store.set_text, pid, text)


def new_version(ws: Workspace, task: str, pid: str) -> str:
    need_prompt_id(pid)
    return call(ws, prompt_store(ws, task).copy_version, pid, set(runs_by_prompt(ws, task)[0]))


def archive(ws: Workspace, task: str, pid: str, archived: bool) -> None:
    need_prompt_id(pid)
    call(ws, prompt_store(ws, task).set_archived, pid, bool(archived))


def set_stages(ws: Workspace, task: str, pid: str, stages) -> str:
    need_prompt_id(pid)
    return call(ws, prompt_store(ws, task).set_stages, pid, stages)
