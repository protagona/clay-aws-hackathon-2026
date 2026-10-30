"""Workflow score v2: a pure function of the trace, run.json and the prompt's expected stages; no model involved.

v2 = v1 (spec: Definitions) plus a `sequence` component when tasks/<task>/expected.jsonl has the prompt
(increment-4 S1-S3). Without an expectation the v1 weights and components apply unchanged."""

from __future__ import annotations

from datetime import datetime

from foundry.contract import RunRecord, Step
from foundry.tasks import Expectation

VERSION = "v2"
WEIGHTS = {"completion": 0.40, "reliability": 0.20, "recovery": 0.15, "validity": 0.10, "efficiency": 0.15}
WEIGHTS_SEQUENCE = {"completion": 0.35, "reliability": 0.15, "recovery": 0.15, "validity": 0.10, "efficiency": 0.10,
                    "sequence": 0.15}
DEFAULT_MAX_MODEL_CALLS = 20


def match_stages(steps: list[Step], stages: list[list[str]]) -> int:
    """How many stages match in order: each is satisfied by the first ok tool call to one of its tools after the
    previous stage's match. A tool is "name" (any server) or "server/name"."""
    matched = 0
    for s in steps:
        if matched == len(stages):
            break
        if s.type != "tool_call" or s.status != "ok":
            continue
        if any(t == s.name or t == f"{s.server}/{s.name}" for t in stages[matched]):
            matched += 1
    return matched


def score(run: RunRecord, steps: list[Step], expected: Expectation | None = None) -> dict:
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
    weights, sequence = WEIGHTS, None
    if expected is not None:
        matched = match_stages(steps, expected.stages)
        components["sequence"] = matched / len(expected.stages)
        weights = WEIGHTS_SEQUENCE
        sequence = {"stages": expected.stages, "matched": matched,
                    "missing": [i + 1 for i in range(matched, len(expected.stages))],
                    "expected_sha256": expected.sha256}
    components = {k: round(v, 4) for k, v in components.items()}
    total = round(sum(weights[k] * v for k, v in components.items()), 4)
    costs = [s.cost_usd for s in steps if s.cost_usd is not None]
    result = {
        "steps": len(steps), "tool_calls": tool_calls, "tool_errors": tool_errors, "retries": retries,
        "recovered": recovered, "invalid_tool_calls": invalid, "model_calls": model_calls,
        "max_model_calls": max_calls, "duration_s": _duration(run, steps),
        "cost_usd": round(sum(costs), 6) if costs else None,
        "components": components, "weights": weights, "score": total,
        "rationale": _rationale(components, run, tool_calls, tool_errors, invalid, model_calls, max_calls, sequence),
    }
    if sequence is not None:
        result["sequence"] = sequence
    return result


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
               max_calls: int, sequence: dict | None = None) -> str:
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
    if sequence is not None and c["sequence"] < 1:
        n, first = len(sequence["stages"]), sequence["matched"]
        lost.append(f"sequence (missed stage {first + 1} of {n}: {'|'.join(sequence['stages'][first])}"
                    + (f", and {n - first - 1} after it" if n - first > 1 else "") + ")")
    if not lost:
        return ("Full marks: the run succeeded with no tool errors or invalid calls, within half the model-call budget"
                + (", and called the expected tools in order." if sequence is not None else "."))
    return "Lost points on " + "; ".join(lost) + "."
