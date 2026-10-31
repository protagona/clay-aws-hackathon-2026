"""The narrative (a summarizer model, word-capped, failure-tolerant) and Struggles (deterministic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files

from foundry.contract import RunRecord, Step
from foundry.engine.models.base import Completion, CompletionRequest, Provider

TEMPLATE = "summarizer-v1"
MAX_WORDS = 150
NO_STRUGGLES = "None: no tool errors, retries, invalid calls or limits hit."


@dataclass
class Narrative:
    text: str | None
    error: str | None = None
    calls: list[Completion] = field(default_factory=list)


def word_count(text: str) -> int:
    return len(text.split())


def narrative(provider: Provider, run_data: str, max_tokens: int) -> Narrative:
    template = files("foundry.engine.evaluate.templates").joinpath(f"{TEMPLATE}.txt").read_text()
    req = CompletionRequest(system="", user=template.replace("{{RUN_DATA}}", run_data), max_tokens=max_tokens,
                            purpose="summary")
    result = Narrative(None)
    errors = []
    for _ in range(2):  # one retry
        try:
            completion = provider.complete(req)
        except Exception as exc:
            errors.append(f"summarizer call failed: {type(exc).__name__}: {exc}"[:300])
            continue
        result.calls.append(completion)
        text = " ".join(completion.text.split())
        if not text:
            errors.append("summarizer returned an empty narrative")
        elif word_count(text) > MAX_WORDS:
            errors.append(f"narrative has {word_count(text)} words (limit {MAX_WORDS})")
        else:
            result.text = text
            return result
    result.error = errors[-1]
    return result


def struggles(steps: list[Step], run: RunRecord) -> list[str]:
    out = []
    for s in steps:
        detail = " ".join((s.error or s.text or "").split())[:160]
        if s.type == "tool_call" and s.invalid:
            out.append(f"Step {s.step}: invalid call to {s.name}: {detail}")
        elif s.type == "tool_call" and s.status == "error":
            out.append(f"Step {s.step}: {s.name} failed: {detail}")
        elif s.type == "llm_call" and s.status == "error":
            out.append(f"Step {s.step}: model call failed: {detail}")
        elif s.type == "note" and s.name == "verdict":  # pipeline checker (increment 7): approval is no struggle
            if s.text == "revise":
                out.append(f"Step {s.step}: the checker asked for a revision")
        elif s.type == "note":
            out.append(f"Step {s.step}: {detail}")
        if s.retry_of is not None:
            out.append(f"Step {s.step}: retried step {s.retry_of} ({s.name}), {'succeeded' if s.status == 'ok' else 'failed again'}")
    if run.status != "succeeded":
        out.append(f"Run ended '{run.status}'.")
    return out or [NO_STRUGGLES]
