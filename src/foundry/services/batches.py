"""Batches: starting one (a background `foundry batch`), a batch's rows and per-variant numbers, and the list."""

from __future__ import annotations

import secrets
import time

from foundry.config import ConfigError
from foundry.data import batches as data_batches
from foundry.engine.batch import spread
from foundry.services import configs, jobs
from foundry.services.errors import ServiceError
from foundry.services.workspace import Workspace

REPEATS = (1, 5)  # inclusive ranges a batch accepts (the API's BatchBody declares the same limits from these)
CONCURRENCY = (1, 4)
MAX_COST = 50.0  # USD; the cap must be above 0 and at most this


def start_batch(ws: Workspace, config: str, models: list[str], prompts: list[str], repeats: int, concurrency: int,
                evaluate: bool, max_cost: float) -> str:
    configs.require_config(ws, config)
    unknown = [m for m in models if m not in configs.model_choices(ws)]
    if unknown:
        raise ServiceError(400, f"Unknown model(s): {', '.join(unknown)}")
    if not prompts:
        raise ServiceError(400, f"Pick one or more prompts from: {', '.join(configs.prompt_ids(ws))}")
    try:
        cfg = configs.load(ws, config)
        configs.check_runnable(ws, cfg, config, prompts, configs.task_prompts(ws, cfg.task.id))
    except ConfigError as exc:
        raise ServiceError(400, configs.relative_paths(ws, str(exc))) from None
    if not (REPEATS[0] <= repeats <= REPEATS[1] and CONCURRENCY[0] <= concurrency <= CONCURRENCY[1]
            and 0 < max_cost <= MAX_COST):
        raise ServiceError(400, f"Repeats must be {REPEATS[0]}-{REPEATS[1]}, parallel runs {CONCURRENCY[0]}-"
                                f"{CONCURRENCY[1]}, and the cost cap between $0 and ${MAX_COST:g}.")
    batch_id = time.strftime("b-%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    args = ["batch", "--config", f"config/{config}.toml", "--prompts", ",".join(prompts),
            "--repeats", str(repeats), "--concurrency", str(concurrency), "--max-cost", f"{max_cost:.2f}",
            "--out", str(ws.batches_dir / batch_id), "--runs-dir", str(ws.runs_dir), "--run-prefix", f"{batch_id}_"]
    if models:
        args += ["--models", ",".join(models)]
    if evaluate:
        args.append("--evaluate")
    # A queued batch has no run dirs yet: its prompt ids keep those prompts' text locked (prompts.set_text).
    jobs.start(ws, "batch", batch_id, f"Batch {batch_id}", args, singleton=True, prompt_ids=prompts,
               task=cfg.task.id)
    return batch_id


def variant_stats(rows: list[dict]) -> list[dict]:
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


def batch_view(ws: Workspace, batch_id: str) -> dict:
    path = data_batches.batch_path(ws.batches_dir, batch_id)
    if path is None:
        raise ServiceError(404, "Batch not found")
    job = jobs.of_run(ws, "batch", batch_id)
    meta = data_batches.read_meta(path)
    rows = data_batches.batch_rows(path, ws.runs_dir)
    running = (job is not None and job.alive) or (job is None and meta.get("status") == "running"
                                                   and not meta.get("exit_code"))
    if job is not None and not job.alive and meta.get("status") != "finished":
        meta["status"] = "failed to start" if job.exit_code == 2 else "stopped"
    return {"batch_id": batch_id, "meta": meta, "rows": rows, "variants": variant_stats(rows), "job": job,
            "running": running, "done": sum(r["status"] not in ("pending", "") for r in rows)}


def batch_list(ws: Workspace) -> list[dict]:
    return data_batches.batch_list(ws.batches_dir, ws.runs_dir)
