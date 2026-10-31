"""Background `foundry` subprocesses the UI starts (runs, evaluations, batches, tool listings) and their logs.

The one services module allowed to touch processes and files directly (tests/test_architecture.py): starting a
subprocess with its stderr in a log file is what it is for. Others call spawn() as `jobs.spawn(...)` through this
module, so tests can replace it in one place.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from foundry.data import locks as lock
from foundry.services.errors import ServiceError
from foundry.services.workspace import Workspace

JOB_ID_RE = re.compile(r"^[a-f0-9]{16}$")
RECENT_JOB_S = 120  # finished jobs reported to the UI for toasts


@dataclass
class Job:
    id: str
    kind: str  # "run" | "evaluate" | "batch" | "tools"
    run_id: str
    label: str
    proc: subprocess.Popen
    log: Path
    exit_code: int | None = None
    dismissed: bool = False
    finished_at: float | None = None  # time.time() when the process exited (toasts in the TypeScript UI)
    lock_token: str | None = None
    prompts: list[str] | None = None  # a batch's prompts, so their text stays locked while it runs (increment 13)

    @property
    def alive(self) -> bool:
        return self.exit_code is None

    @property
    def failed_visibly(self) -> bool:
        """Runs that exit 1 are ordinary failed runs with a card; only exit 2 (nothing created) needs a banner."""
        if self.exit_code is None or self.dismissed:
            return False
        if self.kind in ("run", "batch"):  # exit 1 = ordinary failed run / budget stop, shown on its own page
            return self.exit_code == 2
        return self.exit_code != 0

    def last_stderr_line(self) -> str:
        try:
            lines = [ln for ln in self.log.read_text(errors="replace").splitlines() if ln.strip()]
        except FileNotFoundError:
            return ""
        return lines[-1][:300] if lines else f"exited with code {self.exit_code}"


def spawn(ws: Workspace, kind: str, run_id: str, label: str, args: list[str], lock_token: str | None = None,
          lock_dir: Path | None = None) -> Job:
    job_id = secrets.token_hex(8)
    log_dir = ws.runs_dir / ".ui-jobs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"{job_id}.log"
    env = {**os.environ, "FOUNDRY_HOME": str(ws.root)}
    with open(log, "wb") as errf:
        proc = subprocess.Popen([sys.executable, "-m", "foundry", *args], cwd=ws.root, env=env,
                                stdout=subprocess.DEVNULL, stderr=errf, stdin=subprocess.DEVNULL)
    job = Job(job_id, kind, run_id, label, proc, log, lock_token=lock_token)
    ws.jobs[job_id] = job

    def watch():
        code = proc.wait()
        job.finished_at = time.time()
        job.exit_code = code
        if lock_token and lock_dir is not None:
            lock.release(lock_dir, lock_token)  # whatever the exit code; only if the token still matches

    threading.Thread(target=watch, name=f"foundry-job-{job_id}", daemon=True).start()
    return job


def alive_of(ws: Workspace, kind: str) -> Job | None:
    return next((j for j in list(ws.jobs.values()) if j.kind == kind and j.alive), None)


def recent(ws: Workspace) -> list[dict]:
    """Live jobs, jobs finished in the last RECENT_JOB_S, and failures not yet dismissed (for toasts)."""
    now = time.time()
    out = []
    for j in list(ws.jobs.values()):
        finished = j.finished_at
        if j.alive or (finished and now - finished <= RECENT_JOB_S) or j.failed_visibly:
            out.append({"id": j.id, "kind": j.kind, "run_id": j.run_id, "label": j.label, "alive": j.alive,
                        "exit_code": j.exit_code, "failed": j.failed_visibly, "finished_at": finished,
                        "message": j.last_stderr_line() if j.failed_visibly else None})
    return out


def find(ws: Workspace, job_id: str) -> Job | None:
    return ws.jobs.get(job_id) if JOB_ID_RE.match(job_id) else None


def log_text(ws: Workspace, job_id: str) -> str:
    job = find(ws, job_id)
    if job is None:
        raise ServiceError(404, "Job not found")
    return job.log.read_text(errors="replace") if job.log.exists() else ""


def dismiss(ws: Workspace, job_id: str) -> None:
    job = find(ws, job_id)
    if job is not None:
        job.dismissed = True
