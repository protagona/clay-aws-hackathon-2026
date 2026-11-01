"""What a cloud worker does with one job (aws-deploy design ADR-6): claim it, pull what it needs, run the same
`foundry` command a laptop would (the engine is unchanged), push the results with the run index's card and summary
every few seconds while it runs and at the end, then finish the job exactly once. Each job's start and end, and
its output, go to the `foundry.worker` log, which reaches CloudWatch (spec O1); the output is also kept in the bucket.

Processes and files are reached only through `jobs.run_foundry` and `foundry.data` (the layer rule in
tests/test_architecture.py), so this module is plain orchestration.
"""

from __future__ import annotations

import json
import logging
import time

from foundry.data import index
from foundry.data import runs as data_runs
from foundry.data.mirror import PK as TREE
from foundry.data.store import Conflict, Cond, Update
from foundry.services import cloud_jobs, jobs, runs
from foundry.services.workspace import Workspace

SYNC_EVERY_S = 5.0
OUTPUT_LOG_CHARS = 32_000  # the tail of a job's output that goes to the log (CloudWatch takes 256 KB an event)

log = logging.getLogger("foundry.worker")


def log_output(job_id: str, output: str, failed: bool) -> None:
    """The job's output (its tail when long): WARNING when the job failed, so it shows at any log level."""
    if output.strip():
        tail = output[-OUTPUT_LOG_CHARS:]
        cut = f" (last {OUTPUT_LOG_CHARS} of {len(output)} chars)" if len(output) > OUTPUT_LOG_CHARS else ""
        log.log(logging.WARNING if failed else logging.INFO, "job %s output%s:\n%s", job_id, cut, tail)


def claim(store, job_id: str, now: float, deadline: float | None = None) -> dict | None:
    """queued → running, or None when another delivery already has it (or it was swept). `deadline`: when a job
    that started may be swept as interrupted (spec S3: 17 minutes from its start; a queued job's deadline counted
    the event age too)."""
    changes = {"status": "running", "started_at": now, **({"deadline": deadline} if deadline is not None else {})}
    try:
        store.write(Update(cloud_jobs.PK, job_id, set=changes, cond=Cond.eq("status", "queued")))
    except Conflict:
        return None
    job = store.get_item(cloud_jobs.PK, job_id)
    if deadline is not None:  # the slot and the scoring lock expire with the job, so a start isn't held past it
        for pk, sk, attr in (("SLOT", job.get("slot") or "", "job_id"), ("LOCK", job.get("eval_lock") or "", "owner")):
            if sk:
                try:
                    store.write(Update(pk, sk, set={"expires": deadline}, cond=Cond.eq(attr, job_id)))
                except Conflict:
                    pass
    return job


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
    now = clock()
    job = claim(store, job_id, now, deadline=now + cloud_jobs.RUN_LIMIT_S + cloud_jobs.MARGIN_S)
    if job is None:
        return "duplicate"
    kind, run_id = job["kind"], job["run_id"]
    log.info("job %s start: kind=%s run=%s", job_id, kind, run_id)
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
    cost = actual_cost(ws, kind, run_id, code)
    cloud_jobs.finish(store, job_id, status, code, lines[-1][:300] if lines and code else None, clock(), actual=cost)
    log.info("job %s end: kind=%s run=%s status=%s exit=%s cost=%s duration=%.1fs", job_id, kind, run_id, status, code,
             "unknown" if cost is None else f"${cost:.4f}", clock() - now)
    log_output(job_id, err, failed=code != 0)
    if kind in ("run", "evaluate") and present():
        index_run(ws, mirror, run_id)  # the card without "evaluating" now the lock is released
    return status


# --- batches on Step Functions (aws-deploy design ADR-7) ---------------------------------------------------------
# The workflow: Prepare → Map{batch_item} (each item catches its own errors) → Finalize. The engine's planning,
# derived configs, summary rows and run costs are reused; only the loop moved out of engine/batch.py's thread pool.


def _flag(args: list[str], name: str, default=None):
    return args[args.index(name) + 1] if name in args else default


def batch_prepare(ws: Workspace, store, mirror, batch_job_id: str, clock=time.time) -> dict:
    """Plan the batch from its job's own arguments, write batch.json, the derived configs and an empty summary, and
    return the Map's items."""
    from foundry.data import batches as data_batches
    from foundry.engine.batch import SUMMARY_FIELDS, plan

    claim(store, batch_job_id, clock())  # queued → running (a redelivery finds it running: plan again, same result)
    job = store.get_item(cloud_jobs.PK, batch_job_id)
    args, batch_id = json.loads(job["args"]), job["run_id"]
    pull_projects(store, mirror)
    out = ws.batches_dir / batch_id
    models = [m for m in (_flag(args, "--models") or "").split(",") if m]
    config = _flag(args, "--config")
    planned = plan([str(ws.root / config)], models, _flag(args, "--prompts"), int(_flag(args, "--repeats", "1")), out,
                   _flag(args, "--run-prefix", ""))
    meta = {"id": batch_id, "configs": [config], "models": models, "prompts": _flag(args, "--prompts"),
            "repeats": int(_flag(args, "--repeats", "1")), "concurrency": int(_flag(args, "--concurrency", "2")),
            "evaluate": "--evaluate" in args, "max_cost": float(_flag(args, "--max-cost", "0")) or None,
            "jobs": len(planned), "status": "running", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    data_batches.write_meta(out, meta)
    for n, j in enumerate(planned):
        data_batches.replace_summary_row(out, n, {"variant": j.variant.name, "model": j.variant.model,
                                                  "prompt": j.prompt, "repeat": j.repeat, "status": "pending"},
                                         SUMMARY_FIELDS)
    mirror.push_dir(f"batch#{batch_id}")
    log.info("batch %s start: job=%s items=%d", batch_id, batch_job_id, len(planned))
    items = [{"n": n, "batch_job_id": batch_job_id, "batch_id": batch_id, "variant": j.variant.name,
              "model": j.variant.model, "prompt": j.prompt, "repeat": j.repeat, "run_id": j.run_id,
              "config": _relative_config(ws, j.variant.config, batch_id), "evaluate": meta["evaluate"],
              "eval_config": _relative_config(ws, planned[0].variant.config, batch_id)} for n, j in enumerate(planned)]
    return {"batch_job_id": batch_job_id, "concurrency": meta["concurrency"], "items": items}


def _relative_config(ws: Workspace, path, batch_id: str) -> str:
    """A config path every worker can resolve: project-relative (config/x.toml) or inside the batch tree."""
    from pathlib import Path

    p = Path(path)
    batch_dir = ws.batches_dir / batch_id
    if p.is_relative_to(batch_dir):
        return f"batch:{p.relative_to(batch_dir).as_posix()}"
    return p.relative_to(ws.root).as_posix() if p.is_relative_to(ws.root) else str(p)


def _resolve_config(ws: Workspace, ref: str, batch_id: str) -> str:
    return str(ws.batches_dir / batch_id / ref.removeprefix("batch:")) if ref.startswith("batch:") else ref


def batch_item(ws: Workspace, store, mirror, item: dict, clock=time.time) -> dict:
    """One job of a batch, run once even if Step Functions delivers it twice; its cost is booked on the batch."""
    from foundry.data import batches as data_batches
    from foundry.data.store import Put
    from foundry.engine.batch import SUMMARY_FIELDS, Job, Variant, run_cost, summary_row

    now = clock()
    key = f"{item['batch_job_id']}#{item['n']}"
    try:
        store.write(Put({"pk": cloud_jobs.PK, "sk": key, "kind": "batch_item", "parent": item["batch_job_id"],
                         "run_id": item["run_id"], "label": f"{item['batch_id']} item {item['n']}", "status": "running",
                         "args": "[]", "queued_at": now, "started_at": now,
                         "deadline": now + 2 * cloud_jobs.RUN_LIMIT_S + cloud_jobs.MARGIN_S}, cond=Cond.absent()))
    except Conflict:  # a redelivery: report what the first delivery did (or is doing)
        prior = store.get_item(cloud_jobs.PK, key) or {}
        return {"n": item["n"], "status": prior.get("message") or prior.get("status", "running")}
    batch = store.get_item(cloud_jobs.PK, item["batch_job_id"]) or {}
    max_cost = float(_flag(json.loads(batch.get("args") or "[]"), "--max-cost", "0") or 0)
    variant = Variant(item["variant"], _resolve_config(ws, item["config"], item["batch_id"]), item["model"])
    job = Job(variant, item["prompt"], item["repeat"], "")
    job.run_dir = None
    status, cost = "skipped (budget)", 0.0
    log.info("job %s start: kind=batch_item run=%s", key, item["run_id"])
    if not (max_cost and float(batch.get("spent") or 0) >= max_cost):
        pull_projects(store, mirror)
        mirror.pull(f"batch#{item['batch_id']}")
        run_id = item["run_id"]

        def tick():
            if data_runs.safe_run_path(ws.runs_dir, run_id) is not None:
                index_run(ws, mirror, run_id)

        code, output = jobs.run_foundry(["run", "--config", str(variant.config), "--prompt", item["prompt"],
                                    "--runs-dir", str(ws.runs_dir), "--run-id", run_id], cwd=ws.root, tick=tick)
        log_output(key, output, failed=code != 0)
        d = data_runs.safe_run_path(ws.runs_dir, run_id)
        if code == 2 or d is None:
            status = "error"
        else:
            if item.get("evaluate"):
                jobs.run_foundry(["evaluate", str(d), "--config",
                                  _resolve_config(ws, item["eval_config"], item["batch_id"])], cwd=ws.root)
            index_run(ws, mirror, run_id)
            status, cost, job.run_dir, job.status = "ran", run_cost(d), d, "ran"
    job.status = status if status != "ran" else "ran"
    _book_item_cost(store, batch, item["batch_job_id"], cost)
    with jobs.exclusive_key(ws, f"lease#batch#{item['batch_id']}") as owner:
        mirror.pull(f"batch#{item['batch_id']}")
        data_batches.replace_summary_row(ws.batches_dir / item["batch_id"], item["n"], summary_row(job), SUMMARY_FIELDS)
        mirror.push_dir(f"batch#{item['batch_id']}", lease=(f"lease#batch#{item['batch_id']}", owner))
    cloud_jobs.finish(store, key, "done", 0 if status == "ran" else 1, status, clock())
    log.info("job %s end: kind=batch_item run=%s status=%s cost=$%.4f duration=%.1fs", key, item["run_id"], status, cost,
             clock() - now)
    return {"n": item["n"], "status": status}


def _book_item_cost(store, batch: dict, batch_job_id: str, cost: float) -> None:
    """Into the batch's running total while it runs; straight onto its start month once it has been finalized, so
    no late cost escapes the cap (design ADR-7)."""
    cost = round(cost, 6)  # to the micro-dollar, as spend is settled: no float noise in the running total
    if not cost:
        return
    try:
        store.write(Update(cloud_jobs.PK, batch_job_id, add={"spent": cost}, cond=Cond.eq("status", "running")))
    except Conflict:
        if batch.get("month"):
            store.write(Update("SPEND", batch["month"], add={"committed": cost}))


def batch_finalize(ws: Workspace, store, mirror, batch_job_id: str, error: str | None = None, clock=time.time) -> dict:
    """Close the batch: its real total replaces its reservation (start month), batch.json says finished, the slot
    is released. Runs after every item has returned (each item catches its own errors), or on the error path."""
    from foundry.data import batches as data_batches

    batch = store.get_item(cloud_jobs.PK, batch_job_id) or {}
    batch_id = batch.get("run_id", "")
    spent = float(batch.get("spent") or 0.0)
    mirror.pull(f"batch#{batch_id}")
    path = data_batches.batch_path(ws.batches_dir, batch_id)
    rows = data_batches.batch_rows(path, ws.runs_dir) if path else []
    # An item whose worker died (a timeout or crash: Step Functions caught it) never booked its cost; charge its
    # worst case, so its real Bedrock spend can't escape the batch's budget or the monthly cap (ADR-7 #6).
    item_wc = float(batch.get("item_wc") or 0.0)
    dead = 0
    for it in store.query(cloud_jobs.PK, prefix=f"{batch_job_id}#"):
        if it.get("status") in ("queued", "running") and cloud_jobs.finish(
                store, it["sk"], "interrupted", None, "interrupted: its worker stopped before finishing", clock()):
            dead += 1
            if item_wc:
                _book_item_cost(store, batch, batch_job_id, item_wc)
    bad = error is not None or dead > 0 or any(r["status"] in ("error", "skipped (budget)") for r in rows)
    status = "failed" if error else "done"
    spent = float((store.get_item(cloud_jobs.PK, batch_job_id) or {}).get("spent") or 0.0)  # with dead items' charge
    if path is not None:  # batch.json first: a reader that sees the job finished must see the batch finished too
        meta = data_batches.read_meta(path)
        data_batches.write_meta(path, {**meta, "status": "finished", "exit_code": 1 if bad else 0,
                                       "spent_usd": round(spent, 6), "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        mirror.push_dir(f"batch#{batch_id}")
    for _ in range(5):  # settle on the total we read; an item booking in between makes us re-read (design ADR-7)
        batch = store.get_item(cloud_jobs.PK, batch_job_id) or {}
        spent = float(batch.get("spent") or 0.0)
        if cloud_jobs.finish(store, batch_job_id, status, 1 if bad else 0, error, clock(), actual=spent,
                             expect={"spent": batch.get("spent", 0)}):
            break
        if (store.get_item(cloud_jobs.PK, batch_job_id) or {}).get("status") not in ("queued", "running"):
            break  # already finished (a redelivered Finalize, or the sweep)
    log.info("batch %s end: job=%s status=%s failures=%s interrupted=%d spent=$%.4f%s", batch_id, batch_job_id, status,
             "yes" if bad else "no", dead, spent, f" error={error}" if error else "")
    return {"batch_job_id": batch_job_id, "status": status, "spent": spent}
