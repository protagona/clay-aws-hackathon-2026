"""The state one UI server works in: its project root, its runs directory, and the jobs and receiver it started."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Workspace:
    runs_dir: Path
    root: Path
    port: int
    jobs: dict[str, "Job"] = field(default_factory=dict)  # noqa: F821 (services.jobs.Job; jobs imports this module)
    lock: threading.Lock = field(default_factory=threading.Lock)
    receiver_port: int = 4318
    receiver: object | None = None  # foundry.engine.receiver.Receiver while running
    backend: object | None = None  # services.jobs backend; None = LocalBackend (subprocesses, as on a laptop)

    @property
    def batches_dir(self) -> Path:
        return self.runs_dir / ".batches"
