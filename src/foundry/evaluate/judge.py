"""Output score: a judge model scores each rubric criterion; its reply is parsed and validated strictly."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from importlib.resources import files

from foundry.contract import Rubric
from foundry.models.base import Completion, CompletionRequest, Provider

TEMPLATE = "judge-v1"
NO_OUTPUT_RATIONALE = "No final output was produced."


class JudgeReplyError(ValueError):
    pass


@dataclass
class JudgeResult:
    criteria: list[dict] | None
    score: float | None
    error: str | None = None
    calls: list[Completion] = field(default_factory=list)


def prompt(rubric: Rubric, run_data: str) -> str:
    template = files("foundry.templates").joinpath(f"{TEMPLATE}.txt").read_text()
    rubric_json = json.dumps(rubric.model_dump(), indent=2)
    return template.replace("{{RUBRIC_JSON}}", rubric_json).replace("{{RUN_DATA}}", run_data)


def output_score(rubric: Rubric, criteria: list[dict]) -> float:
    by_id = {c.id: c for c in rubric.criteria}
    total_w = sum(c.weight for c in rubric.criteria)
    s = sum(by_id[e["id"]].weight * (e["score"] - 1) / (by_id[e["id"]].max - 1) for e in criteria)
    return round(s / total_w, 4)


def parse(text: str, rubric: Rubric) -> list[dict]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise JudgeReplyError("reply is not JSON")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise JudgeReplyError(f"reply is not valid JSON ({exc.msg})") from None
    entries = data.get("criteria") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise JudgeReplyError('reply has no "criteria" list')
    by_id = {c.id: c for c in rubric.criteria}
    got = [e.get("id") if isinstance(e, dict) else None for e in entries]
    missing = sorted(set(by_id) - set(got))
    unknown = sorted({g for g in got if g not in by_id}, key=str)
    if missing or unknown or len(got) != len(set(got)):
        parts = []
        if missing:
            parts.append(f"missing criteria {missing}")
        if unknown:
            parts.append(f"unknown criteria {unknown}")
        if len(got) != len(set(got)):
            parts.append("duplicate criteria")
        raise JudgeReplyError("; ".join(parts))
    out = []
    for e in entries:
        c = by_id[e["id"]]
        sc = e.get("score")
        if not isinstance(sc, int) or isinstance(sc, bool) or not 1 <= sc <= c.max:
            raise JudgeReplyError(f"criterion '{c.id}' score {sc!r} is not an integer in [1, {c.max}]")
        rationale = e.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise JudgeReplyError(f"criterion '{c.id}' has an empty rationale")
        out.append({"id": c.id, "score": sc, "max": c.max, "rationale": rationale.strip()})
    order = [c.id for c in rubric.criteria]
    return sorted(out, key=lambda e: order.index(e["id"]))


def judge(provider: Provider, rubric: Rubric, run_data: str, output_kind: str, max_tokens: int) -> JudgeResult:
    if output_kind == "none":
        criteria = [{"id": c.id, "score": 1, "max": c.max, "rationale": NO_OUTPUT_RATIONALE} for c in rubric.criteria]
        return JudgeResult(criteria, output_score(rubric, criteria))
    req = CompletionRequest(system="", user=prompt(rubric, run_data), max_tokens=max_tokens, purpose="judge",
                            rubric=rubric)
    result = JudgeResult(None, None)
    errors = []
    for _ in range(2):  # one retry
        try:
            completion = provider.complete(req)
        except Exception as exc:
            errors.append(f"judge call failed: {type(exc).__name__}: {exc}"[:300])
            continue
        result.calls.append(completion)
        try:
            criteria = parse(completion.text, rubric)
        except JudgeReplyError as exc:
            errors.append(f"malformed judge reply: {exc}")
            continue
        result.criteria, result.score = criteria, output_score(rubric, criteria)
        return result
    result.error = errors[-1]
    return result
