"""The tracer seam. The harness produces one Step stream; every configured tracer receives the same steps."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Protocol

from foundry.contract import RunRecord, Step
from foundry.data.runs import utc_now


class Tracer(Protocol):
    def start_run(self, run: RunRecord) -> None: ...

    def record_step(self, step: Step) -> None: ...

    def end_run(self, run: RunRecord) -> None: ...


class TracerSet:
    """Numbers steps, fans them out, and drops anything recorded after close() (late worker threads)."""

    def __init__(self, tracers: list[Tracer]):
        self.tracers = tracers
        self._lock = threading.Lock()
        self._next = 1
        self._closed = False
        self.steps: list[Step] = []

    def start_run(self, run: RunRecord) -> None:
        for t in self.tracers:
            t.start_run(run)

    def record(self, **fields: Any) -> Step | None:
        fields.setdefault("started_at", utc_now())
        fields.setdefault("duration_ms", 0)
        fields.setdefault("status", "ok")
        with self._lock:
            if self._closed:
                return None
            step = Step(step=self._next, **fields)
            self._next += 1
            self.steps.append(step)
            for t in self.tracers:
                try:
                    t.record_step(step)
                except Exception as exc:  # a broken optional tracer must not break the run
                    print(f"warning: tracer {type(t).__name__} failed to record step: {exc}", file=sys.stderr)
            return step

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def end_run(self, run: RunRecord) -> None:
        for t in self.tracers:
            try:
                t.end_run(run)
            except Exception as exc:
                print(f"warning: tracer {type(t).__name__} failed at end of run: {exc}", file=sys.stderr)


def build_tracers(tracing_cfg, run_dir: Path) -> TracerSet:
    from foundry.engine.tracing.jsonl import JsonlTracer

    tracers: list[Tracer] = [JsonlTracer(run_dir)]
    if "otel" in tracing_cfg.tracers:
        from foundry.engine.tracing.otel import OtelTracer

        tracers.append(OtelTracer(tracing_cfg.otel))
    return TracerSet(tracers)


def tracer_label(tracing_cfg) -> str:
    return "+".join(tracing_cfg.tracers)
