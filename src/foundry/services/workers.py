"""What a cloud worker does with one job (aws-deploy design ADR-6): claim it, pull what it needs, run the same
`foundry` command a laptop would (the engine is unchanged), push the results with the run index's card and summary
every few seconds while it runs and at the end, then finish the job exactly once.

Processes and files are reached only through `jobs.run_foundry` and `foundry.data` (the layer rule in
tests/test_architecture.py), so this module is plain orchestration.
"""

from __future__ import annotations

import json
import time

from foundry.data import index
from foundry.data import runs as data_runs
from foundry.data.mirror import PK as TREE
from foundry.data.store import Conflict, Cond, Update
from foundry.services import cloud_jobs, jobs, runs
from foundry.services.workspace import Workspace

SYNC_EVERY_S = 5.0


def claim(store, job_id: str, now: float) -> dict | None:
    """queued → running, or None when another delivery already has it (or it was swept)."""
    try:
        store.write(Update(cloud_jobs.PK, job_id, set={"status": "running", "started_at": now},
                           cond=Cond.eq("status", "queued")))
    except Conflict:
        return None
    return store.get_item(cloud_jobs.PK, job_id)


def pull_projects(store, mirror) -> None:
    """Every task's project tree (prompts, expected steps, rubrics, history): what runs and scorings read."""
    for it in store.query(TREE, prefix="project#"):
        mirror.pull(it["sk"])


def index_run(ws: Workspace, mirror, run_id: str) -> None:
    """Push the run's changed files together with its board card and summary."""
    d = ws.runs_dir / run_id
    summary = runs.summary_for(d)
    card = runs.card_for(ws, d)
    if not card.get("unreadable"):
        card = {**card, **runs.card_extras(ws, run_id)}
    attrs = index.run_attrs(card, summary) if summary is not None else None
    mirror.push_dir(f"run#{run_id}", attrs=attrs)


def local_args(ws: Workspace, kind: str, run_id: str, args: list[str]) -> list[str]:
    """The job's command with this worker's own paths: the API instance that queued it may lay out /tmp differently."""
    out = list(args)
    for flag, value in (("--runs-dir", ws.runs_dir), ("--out", ws.runs_dir / ".ui-tools.json")):
        if flag in out and (flag != "--out" or kind == "tools"):
            out[out.index(flag) + 1] = str(value)
    if kind == "evaluate" and len(out) > 1:
        out[1] = str(ws.runs_dir / run_id)
    return out


def actual_cost(ws: Workspace, kind: str, run_id: str, code: int) -> float | None:
    """What the job really cost, or None when unknown (then its reservation is charged; spec A5)."""
    if kind == "tools":
        return 0.0
    d = data_runs.safe_run_path(ws.runs_dir, run_id)
    if d is None:
        return None
    if kind == "run":
        s = runs.summary_for(d)
        return s["spent"] if s is not None else None
    if kind == "evaluate" and code == 0:  # a failed scoring may leave an older scores.json: charge the reservation
        ev = (data_runs.read_scores(d) or {}).get("evaluation")
        cost = data_runs.num(ev.get("cost_usd")) if isinstance(ev, dict) else None
        return cost
    return None


def run_job(ws: Workspace, store, mirror, job_id: str, clock=time.time) -> str:
    """Do one job. Returns its final status, or "duplicate" when this delivery wasn't the one to run it."""
    job = claim(store, job_id, clock())
    if job is None:
        return "duplicate"
    kind, run_id = job["kind"], job["run_id"]
    args = local_args(ws, kind, run_id, json.loads(job["args"]))
    pull_projects(store, mirror)
    tick = None
    def present() -> bool:
        return data_runs.safe_run_path(ws.runs_dir, run_id) is not None

    if kind == "run":
        def tick():
            if present():
                index_run(ws, mirror, run_id)
    elif kind == "evaluate":
        mirror.pull(f"run#{run_id}")
    code, err = jobs.run_foundry(args, cwd=ws.root, tick=tick, every=SYNC_EVERY_S)
    if kind == "evaluate" and present():
        index_run(ws, mirror, run_id)  # scores, report, copied rubric, card (still "evaluating": lock held)
    elif kind == "tools":
        mirror.push_file("file#tools", "ui-tools.json", ws.runs_dir / ".ui-tools.json")
    store.put_object(f"jobs/{job_id}.log", err.encode(), if_none_match=False)
    lines = [ln for ln in err.splitlines() if ln.strip()]
    status = "done" if code == 0 else "failed"
    cloud_jobs.finish(store, job_id, status, code, lines[-1][:300] if lines and code else None, clock(),
                      actual=actual_cost(ws, kind, run_id, code))
    if kind in ("run", "evaluate") and present():
        index_run(ws, mirror, run_id)  # the card without "evaluating" now the lock is released
    return status
