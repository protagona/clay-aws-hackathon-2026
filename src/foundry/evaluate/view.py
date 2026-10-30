"""The capped, delimited view of a run that the judge and summarizer receive."""

from __future__ import annotations

import re

from foundry.contract import Output, RunRecord, Step

OUTPUT_CHARS = 20_000
DETAIL_CHARS = 200
HEAD_STEPS = 100
TAIL_STEPS = 100
_CLOSE_TAG = re.compile(r"<\s*/\s*run_data\s*>", re.IGNORECASE)


def neutralize(text: str) -> str:
    """Run content can't close the data block early."""
    return _CLOSE_TAG.sub(r"<\\/run_data>", text)


def step_line(s: Step) -> str:
    bits = [f"#{s.step}", s.type, s.name, s.status, f"{s.duration_ms:g}ms"]
    if s.server:
        bits.append(f"server={s.server}")
    if s.retry_of is not None:
        bits.append(f"retry_of=#{s.retry_of}")
    if s.invalid:
        bits.append("invalid")
    detail = s.error or s.result_preview or s.text or ""
    detail = " ".join(detail.split())[:DETAIL_CHARS]
    return " ".join(bits) + (f" | {detail}" if detail else "")


def timeline(steps: list[Step]) -> list[str]:
    if len(steps) <= HEAD_STEPS + TAIL_STEPS:
        return [step_line(s) for s in steps]
    omitted = len(steps) - HEAD_STEPS - TAIL_STEPS
    return ([step_line(s) for s in steps[:HEAD_STEPS]] + [f"... {omitted} steps omitted ..."]
            + [step_line(s) for s in steps[-TAIL_STEPS:]])


def build(run: RunRecord, steps: list[Step], output: Output) -> str:
    text = output.text
    if len(text) > OUTPUT_CHARS:
        text = text[:OUTPUT_CHARS] + f"\n[output truncated: {len(output.text) - OUTPUT_CHARS} more characters]"
    body = "\n".join([
        f"TASK PROMPT: {run.prompt or '(not recorded)'}",
        f"RUN STATUS: {run.status}",
        f"OUTPUT KIND: {output.kind}",
        "OUTPUT:",
        text,
        "",
        "TIMELINE:",
        *timeline(steps),
    ])
    return f"<run_data>\n{neutralize(body)}\n</run_data>"
