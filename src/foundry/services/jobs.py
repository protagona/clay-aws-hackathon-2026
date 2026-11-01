"""Background jobs the UI starts (runs, evaluations, batches, tool listings), their state and their logs.

Services start and read jobs only through this module (`start`, `live`, `of_kind`, `of_run`, `pending_prompt`,
`is_evaluating`, `exclusive`), never through `ws.jobs` or `ws.lock` directly, so the backend can change: locally
(LocalBackend) jobs are `foundry` subprocesses tracked in memory; in the cloud (aws-deploy design ADR-6) they are
DynamoDB items and worker invocations.

The one services module allowed to touch processes and files directly (tests/test_architecture.py): starting a
subprocess with its stderr in a log file is what it is for. Tests replace `spawn` (as `jobs.spawn`) in one place.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from foundry.data import locks as lock
from foundry.data import spend as local_spend
from foundry.services import job_kinds, spend
from foundry.services.errors import ServiceError
from foundry.services.workspace import Workspace

JOB_ID_RE = re.compile(r"^[a-f0-9]{16}$")
RECENT_JOB_S = 120  # finished jobs reported to the UI for toasts


@dataclass
class Job:
    id: str
    kind: str  # a services.job_kinds name
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
    def status(self) -> str:
        return "running" if self.alive else "done"

    @property
    def failed_visibly(self) -> bool:
        return job_kinds.failed_visibly(self.kind, self.exit_code, self.status, self.alive, self.dismissed)

    def read_log(self) -> str:
        return self.log.read_text(errors="replace") if self.log.exists() else ""

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


def alive_of(ws: Workspace, kind: str):
    return next(iter(live(ws, kind)), None)


def recent(ws: Workspace) -> list[dict]:
    """Live jobs, jobs finished in the last RECENT_JOB_S, and failures not yet dismissed (for toasts)."""
    now = time.time()
    out = []
    for j in backend(ws).all(ws):
        finished = j.finished_at
        if j.alive or (finished and now - finished <= RECENT_JOB_S) or j.failed_visibly:
            out.append({"id": j.id, "kind": j.kind, "run_id": j.run_id, "label": j.label, "alive": j.alive,
                        "exit_code": j.exit_code, "failed": j.failed_visibly, "finished_at": finished,
                        "message": j.last_stderr_line() if j.failed_visibly else None})
    return out


def find(ws: Workspace, job_id: str):
    return next((j for j in backend(ws).all(ws) if j.id == job_id), None) if JOB_ID_RE.match(job_id) else None


def log_text(ws: Workspace, job_id: str) -> str:
    job = find(ws, job_id)
    if job is None:
        raise ServiceError(404, "Job not found")
    return job.read_log()


def dismiss(ws: Workspace, job_id: str) -> None:
    job = find(ws, job_id)
    if job is not None:
        backend(ws).dismiss(ws, job)


# --- the backend seam (aws-deploy slice 3) ----------------------------------------------------------------------

class LocalBackend:
    """Jobs as `foundry` subprocesses of this server, tracked in `ws.jobs` and serialized by `ws.lock` (as before)."""

    def start(self, ws: Workspace, kind: str, run_id: str, label: str, args: list[str], *, singleton: bool,
              prompt_ids: list[str], evaluate_dir: Path | None, quiet: bool, task: str | None,  # noqa: ARG002
              reservation: float = 0.0) -> Job | None:
        token = None
        if evaluate_dir is not None:
            try:
                token = lock.acquire(evaluate_dir, owner="ui")
            except lock.LockHeld as exc:
                raise ServiceError(409, str(exc)) from None
            args = [*args, "--lock-held", token]
        with ws.lock:
            active = alive_of(ws, kind) if singleton else None
            refused = None
            if active is None and ws.cap_usd is not None and reservation > 0:
                committed = local_spend.month_total(ws.runs_dir, spend.month())
                if committed + reservation > ws.cap_usd:
                    refused = spend.refusal(committed, ws.cap_usd)
            if active is not None or refused is not None:
                if token:
                    lock.release(evaluate_dir, token)
                if refused is not None:
                    raise refused
                if quiet:
                    return None
                raise ServiceError(409, job_kinds.kind(kind).busy.format(active.run_id))
            job = spawn(ws, kind, run_id, label, args, lock_token=token, lock_dir=evaluate_dir)
            if ws.cap_usd is not None and reservation > 0:
                local_spend.charge(ws.runs_dir, spend.month(), job.id, reservation)  # locally: charged in full
            job.prompts = list(prompt_ids) or None
        return job

    def all(self, ws: Workspace) -> list[Job]:
        return list(ws.jobs.values())

    def reserve(self, ws: Workspace, kind: str, label: str, reservation: float):  # noqa: ARG002
        with ws.lock:
            committed = local_spend.month_total(ws.runs_dir, spend.month())
            if committed + reservation > ws.cap_usd:
                raise spend.refusal(committed, ws.cap_usd)
            local_spend.charge(ws.runs_dir, spend.month(), f"{kind}-{secrets.token_hex(4)}", reservation)
        return True

    def settle(self, ws: Workspace, handle, actual) -> None:  # noqa: ARG002 (locally the reservation is the charge)
        return None

    def dismiss(self, ws: Workspace, job: Job) -> None:  # noqa: ARG002
        job.dismissed = True

    def is_evaluating(self, ws: Workspace, run_dir: Path) -> bool:
        return lock.is_locked(run_dir)

    @contextmanager
    def exclusive(self, ws: Workspace, task: str):  # noqa: ARG002 (the cloud backend leases per task)
        with ws.lock:
            yield

    @contextmanager
    def exclusive_key(self, ws: Workspace, key: str):  # noqa: ARG002
        with ws.lock:
            yield


LOCAL = LocalBackend()


def backend(ws: Workspace):
    return ws.backend or LOCAL


def start(ws: Workspace, kind: str, run_id: str, label: str, args: list[str], *, singleton: bool = False,
          prompt_ids: list[str] | tuple = (), evaluate_dir: Path | None = None, quiet: bool = False,
          task: str | None = None, reservation: float = 0.0) -> Job | None:
    """Start a job atomically with its checks: `singleton` refuses (409) while another job of the kind is live,
    `evaluate_dir` takes the run's evaluation lock first, and a run of `task` waits out an edit of that task's
    prompts (cloud: their lease). With `quiet`, a busy singleton returns None instead."""
    if not job_kinds.kind(kind).startable:
        raise ValueError(f"job kind {kind!r} is a reservation only")
    return backend(ws).start(ws, kind, run_id, label, args, singleton=singleton, prompt_ids=list(prompt_ids),
                             evaluate_dir=evaluate_dir, quiet=quiet, task=task, reservation=reservation)


def note(ws: Workspace, job, **attrs) -> None:
    """Extra attributes on a cloud job's record (a laptop's in-memory jobs keep none)."""
    fn = getattr(backend(ws), "note", None)
    if fn is not None and job is not None:
        fn(job.id, attrs)


def reserve(ws: Workspace, kind: str, label: str, reservation: float):
    """Spend held for work done in this request (a rubric draft), not by a worker: a handle for `settle`, or None
    when there's nothing to hold. Raises the cap's 409 like a job start."""
    job_kinds.kind(kind)  # declared, or a ValueError
    if reservation <= 0 or ws.cap_usd is None:
        return None
    return backend(ws).reserve(ws, kind, label, reservation)


def settle(ws: Workspace, handle, actual: float | None) -> None:
    """Close a `reserve`: its actual cost replaces the reservation (None = unknown: the reservation is charged)."""
    if handle is not None:
        backend(ws).settle(ws, handle, actual)


def run_foundry(args: list[str], cwd: Path, env: dict | None = None, tick=None, every: float = 5.0,
                timeout: float | None = None) -> tuple[int, str]:
    """Run `python -m foundry <args>` to completion in this process's environment (a cloud worker), calling `tick()`
    every `every` seconds while it runs and once at the end. Returns (exit code, stderr)."""
    log = Path(os.environ.get("TMPDIR", "/tmp")) / f"foundry-job-{secrets.token_hex(4)}.log"
    started = time.time()
    with open(log, "wb") as errf:
        proc = subprocess.Popen([sys.executable, "-m", "foundry", *args], cwd=cwd, env={**os.environ, **(env or {})},
                                stdout=subprocess.DEVNULL, stderr=errf, stdin=subprocess.DEVNULL)
        while True:
            try:
                code = proc.wait(timeout=every)
                break
            except subprocess.TimeoutExpired:
                if timeout is not None and time.time() - started > timeout:
                    proc.kill()
                    code = proc.wait()
                    break
                if tick is not None:
                    tick()
    if tick is not None:
        tick()
    text = log.read_text(errors="replace")
    log.unlink(missing_ok=True)
    return code, text


def of_kind(ws: Workspace, kind: str) -> list:
    """Every known job of a kind, oldest first."""
    return [j for j in backend(ws).all(ws) if j.kind == kind]


def live(ws: Workspace, kind: str | None = None) -> list:
    return [j for j in backend(ws).all(ws) if j.alive and (kind is None or j.kind == kind)]


def of_run(ws: Workspace, kind: str, run_id: str):
    return next((j for j in reversed(of_kind(ws, kind)) if j.run_id == run_id), None)


def pending_prompt(ws: Workspace, pid: str) -> bool:
    """A live UI run of this prompt, or a live batch that includes it."""
    for j in live(ws):
        if j.kind == "run" and f"_{pid}_" in j.run_id:
            return True
        if j.kind == "batch" and pid in (j.prompts or []):
            return True
    return False


def is_evaluating(ws: Workspace, run_dir: Path) -> bool:
    return backend(ws).is_evaluating(ws, run_dir)


def exclusive_key(ws: Workspace, key: str):
    """A named lease (cloud) or the server lock (local): a batch's summary rewrite."""
    return backend(ws).exclusive_key(ws, key)


def exclusive(ws: Workspace, task: str):
    """Hold off job starts while a prompt's text changes (locally the server lock; in the cloud the task's lease)."""
    return backend(ws).exclusive(ws, task)
