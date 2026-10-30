"""The always-on tracer: appends each step to runs/<run_id>/trace.jsonl and flushes it immediately."""

from __future__ import annotations

from pathlib import Path

from foundry.contract import RunRecord, Step
from foundry.runstore import RunDir


class JsonlTracer:
    def __init__(self, run_dir: Path):
        self.run_dir = RunDir(run_dir)

    def start_run(self, run: RunRecord) -> None:
        self.run_dir.touch_trace()

    def record_step(self, step: Step) -> None:
        self.run_dir.append_step(step)

    def end_run(self, run: RunRecord) -> None:
        pass
