"""Workflow score v1: a pure function of the trace and run.json, no model involved (spec: Definitions)."""

from __future__ import annotations

from datetime import datetime

from foundry.contract import RunRecord, Step

VERSION = "v1"
WEIGHTS = {"completion": 0.40, "reliability": 0.20, "recovery": 0.15, "validity": 0.10, "efficiency": 0.15}
DEFAULT_MAX_MODEL_CALLS = 20


def score(run: RunRecord, steps: list[Step]) -> dict:
    tools = [s for s in steps if s.type == "tool_call"]
    tool_calls = len(tools)
    tool_errors = sum(s.status == "error" for s in tools)
    invalid = sum(bool(s.invalid) for s in tools)
    retries = sum(s.retry_of is not None for s in tools)
    model_calls = sum(s.type == "llm_call" for s in steps)
    succeeded = run.status == "succeeded"
    recovered = (succeeded if tool_errors else None)
    max_calls = (run.limits.max_model_calls if run.limits and run.limits.max_model_calls else None) \
        or DEFAULT_MAX_MODEL_CALLS

    if model_calls <= max_calls / 2:
        efficiency = 1.0
    else:
        efficiency = max(0.0, 2 * (1 - model_calls / max_calls))
    components = {
        "completion": 1.0 if succeeded else 0.0,
        "reliability": 1 - tool_errors / tool_calls if tool_calls else 1.0,
        "recovery": 1.0 if (tool_errors == 0 or recovered) else 0.0,
        "validity": 1 - invalid / tool_calls if tool_calls else 1.0,
        "efficiency": efficiency,
    }
    components = {k: round(v, 4) for k, v in components.items()}
    total = round(sum(WEIGHTS[k] * v for k, v in components.items()), 4)
    costs = [s.cost_usd for s in steps if s.cost_usd is not None]
    return {
        "steps": len(steps), "tool_calls": tool_calls, "tool_errors": tool_errors, "retries": retries,
        "recovered": recovered, "invalid_tool_calls": invalid, "model_calls": model_calls,
        "max_model_calls": max_calls, "duration_s": _duration(run, steps),
        "cost_usd": round(sum(costs), 6) if costs else None,
        "components": components, "weights": WEIGHTS, "score": total,
        "rationale": _rationale(components, run, tool_calls, tool_errors, invalid, model_calls, max_calls),
    }


def _duration(run: RunRecord, steps: list[Step]) -> float | None:
    try:
        if run.ended_at:
            fmt = "%Y-%m-%dT%H:%M:%S.%f%z"
            start = datetime.strptime(run.started_at.replace("Z", "+0000"), fmt)
            end = datetime.strptime(run.ended_at.replace("Z", "+0000"), fmt)
            return round((end - start).total_seconds(), 3)
    except ValueError:
        pass
    return round(sum(s.duration_ms for s in steps) / 1000, 3) if steps else None


def _rationale(c: dict, run: RunRecord, tool_calls: int, tool_errors: int, invalid: int, model_calls: int,
               max_calls: int) -> str:
    lost = []
    if c["completion"] < 1:
        lost.append(f"completion (run ended '{run.status}')")
    if c["reliability"] < 1:
        lost.append(f"reliability ({tool_errors} of {tool_calls} tool calls errored)")
    if c["recovery"] < 1:
        lost.append("recovery (tool errors and the run did not succeed)")
    if c["validity"] < 1:
        lost.append(f"validity ({invalid} invalid tool call{'s' if invalid != 1 else ''})")
    if c["efficiency"] < 1:
        lost.append(f"efficiency ({model_calls} of {max_calls} allowed model calls)")
    if not lost:
        return "Full marks: the run succeeded with no tool errors or invalid calls, within half the model-call budget."
    return "Lost points on " + "; ".join(lost) + "."
