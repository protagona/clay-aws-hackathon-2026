"""Evaluate one run directory: workflow score, judge, narrative, report; written together, never the run itself."""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path

from foundry import storage
from foundry.config import EvalConfig
from foundry.contract import CONTRACT_VERSION, validate_run_dir
from foundry.evaluate import judge as judge_mod
from foundry.evaluate import lock, report, summarize, view, workflow
from foundry.models.registry import build_provider
from foundry.runstore import RunDir, dump_line, utc_now
from foundry.tasks import RubricError, load_rubric, rubric_path


class EvaluateError(Exception):
    """A precondition failed; nothing was written (CLI exit 2)."""


def evaluate(run_dir: str | Path, cfg: EvalConfig, rubric_file: str | Path | None = None, force: bool = False,
             lock_token: str | None = None) -> dict:
    rd = RunDir(run_dir)
    if not rd.path.is_dir():
        raise EvaluateError(f"Not a run directory: {rd.path}")
    run = _precheck(rd, force)

    if lock_token is not None:
        if not lock.holds(rd.path, lock_token):
            raise EvaluateError(f"--lock-held token does not match {rd.path / lock.LOCK}")
        token = lock_token
    else:
        try:
            token = lock.acquire(rd.path, owner="cli")
        except lock.LockHeld as exc:
            raise EvaluateError(str(exc)) from None
    try:
        rd.clean_stale_staging()
        scores, files = _evaluate_locked(rd, run, cfg, rubric_file, force)
        rd.atomic_write_many(files)
        storage.mirror(rd.path, cfg.storage)
        return scores
    finally:
        lock.release(rd.path, token)


def _precheck(rd: RunDir, force: bool):
    errors = [v for v in validate_run_dir(rd.path) if v.severity == "error"]
    if errors:
        raise EvaluateError("Run directory fails validation:\n" + "\n".join(str(v) for v in errors))
    run = rd.read_run()
    if run.status == "running" and not force:
        raise EvaluateError(f"Run {run.run_id} is still running. If it crashed, pass --force to evaluate it as failed.")
    return run


def _evaluate_locked(rd: RunDir, run, cfg: EvalConfig, rubric_file, force: bool) -> tuple[dict, dict[str, bytes]]:
    path = Path(rubric_file) if rubric_file else rubric_path(run.task_id)
    try:
        rubric, rubric_bytes = load_rubric(path)
    except RubricError as exc:
        raise EvaluateError(str(exc)) from None

    raw_lines = [ln for ln in (rd.path / "trace.jsonl").read_text().splitlines() if ln.strip()]
    steps = rd.read_steps(drop_truncated_last=force and run.status == "running")
    truncated = len(raw_lines) - len(steps)
    try:
        output = rd.read_output()
    except FileNotFoundError:
        from foundry.contract import Output

        output = Output(kind="none", text="")
    effective = run.model_copy(update={"status": "failed"}) if (force and run.status == "running") else run

    if run.rubric_id and run.rubric_id != rubric.rubric_id:
        print(f"warning: rubric id {rubric.rubric_id!r} differs from run.json's {run.rubric_id!r}", file=sys.stderr)
    if cfg.judge.id == run.model.id:
        print(f"warning: judge model {cfg.judge.id!r} is the same as the agent model; it is grading itself",
              file=sys.stderr)

    wf = workflow.score(effective, steps)
    run_data = view.build(effective, steps, output)
    judge_provider = build_provider(cfg.judge)
    summ_provider = build_provider(cfg.summarizer)
    jr = judge_mod.judge(judge_provider, rubric, run_data, output.kind, cfg.judge.max_tokens)
    nr = summarize.narrative(summ_provider, run_data, cfg.summarizer.max_tokens)

    output_section = {"criteria": jr.criteria, "score": jr.score, "rubric_version": rubric.version,
                      "judge": {"provider": cfg.judge.provider, "id": cfg.judge.id}}
    if jr.error:
        output_section["error"] = jr.error
    overall = None if jr.score is None else round(
        cfg.scoring.workflow_weight * wf["score"] + cfg.scoring.output_weight * jr.score, 4)

    calls = [(c, cfg.judge.id) for c in jr.calls] + [(c, cfg.summarizer.id) for c in nr.calls]
    tokens_in = sum(c.tokens_in for c, _ in calls)
    tokens_out = sum(c.tokens_out for c, _ in calls)
    costs = [_cost(cfg, mid, c.tokens_in, c.tokens_out, c.tokens_in_cached) for c, mid in calls]
    known = [c for c in costs if c is not None]

    scores = {
        "contract_version": CONTRACT_VERSION,
        "evaluation_id": str(uuid.uuid4()),
        "evaluated_at": utc_now(),
        "forced": bool(force and run.status == "running"),
        "truncated_trace_lines": truncated,
        "config": cfg.path,
        "rubric_id": run.rubric_id,
        "rubric_id_loaded": rubric.rubric_id,
        "rubric_version": rubric.version,
        "rubric_id_mismatch": bool(run.rubric_id and run.rubric_id != rubric.rubric_id),
        "templates": {"judge": judge_mod.TEMPLATE, "summarizer": summarize.TEMPLATE},
        "workflow_scorer": workflow.VERSION,
        "weights": {"workflow": cfg.scoring.workflow_weight, "output": cfg.scoring.output_weight},
        "workflow": wf,
        "output": output_section,
        "overall": overall,
        "narrative": nr.text,
        "narrative_error": nr.error,
        "struggles": summarize.struggles(steps, effective),
        "evaluation": {
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "cost_usd": round(sum(known), 6) if known and len(known) == len(costs) else None,
            "judge": {"provider": cfg.judge.provider, "id": cfg.judge.id},
            "summarizer": {"provider": cfg.summarizer.provider, "id": cfg.summarizer.id},
        },
    }
    report_bytes = report.build(effective, steps, output, scores).encode()
    scores["rubric_sha256"] = hashlib.sha256(rubric_bytes).hexdigest()
    scores["report_sha256"] = hashlib.sha256(report_bytes).hexdigest()
    scores_bytes = (dump_line(scores) + "\n").encode()
    # scores.json goes last: it carries the hashes of the other two, so a torn write is detectable.
    return scores, {"rubric.json": rubric_bytes, "report.md": report_bytes, "scores.json": scores_bytes}


def _cost(cfg: EvalConfig, model_id: str, tokens_in: int, tokens_out: int, cached: int = 0) -> float | None:
    price = cfg.prices.get(model_id)
    if price is None:
        return None
    return price.cost(tokens_in, tokens_out, cached)


def load_scores(run_dir: Path) -> dict | None:
    try:
        return json.loads((run_dir / "scores.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
