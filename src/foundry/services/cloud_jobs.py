"""The cloud job backend (aws-deploy design ADR-6): jobs are JOB items in the table and run in worker invocations.

Starting a job is one transaction, so its checks hold across every API instance:
    JOB put (absent) · SLOT put (singletons: absent or expired) · LOCK eval#<run> put (evaluations: absent or expired)
    · a ConditionCheck that no prompt edit of the run's task holds its lease
then an async invoke of the worker (retries 0). A worker claims its job (queued → running) before doing anything, so
a duplicate delivery exits. Finishing is one conditional update (status still queued/running), so a job is finished
exactly once, by its worker or by the sweep that marks a job past its deadline `interrupted`; the slot and the
evaluation lock are then released with separate conditional deletes.
"""

from __future__ import annotations

import json
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from foundry.data.store import Check, Conflict, Cond, Delete, NotFound, Put, Update
from foundry.services import spend
from foundry.services.errors import ServiceError
from foundry.services.jobs import BUSY
from foundry.services.workspace import Workspace

PK = "JOB"
EVENT_AGE_S = 900  # the worker's MaximumEventAgeInSeconds: a job not started by then never will be
RUN_LIMIT_S = 900  # the worker's timeout
MARGIN_S = 120
FAR = 2**40  # "no expiry" (a batch's slot is released by its workflow)
LEASE_S = 120


def lease_key(task: str) -> str:
    return f"lease#project#{task}"


@dataclass
class JobRecord:
    """A JOB item, with the same attributes the API reads from a local Job."""

    id: str
    kind: str
    run_id: str
    label: str
    status: str
    exit_code: int | None
    finished_at: float | None
    dismissed: bool
    prompts: list[str] | None
    message: str | None
    store: object = None

    @property
    def alive(self) -> bool:
        return self.status in ("queued", "running")

    @property
    def failed_visibly(self) -> bool:
        if self.alive or self.dismissed:
            return False
        if self.status == "interrupted":
            return True
        if self.kind in ("run", "batch"):
            return self.exit_code == 2
        return self.exit_code not in (0, None)

    def last_stderr_line(self) -> str:
        return self.message or (f"exited with code {self.exit_code}" if self.exit_code is not None else "")

    def read_log(self) -> str:
        try:
            return self.store.get_object(f"jobs/{self.id}.log").decode(errors="replace")
        except NotFound:
            return ""


def record(item: dict, store) -> JobRecord:
    return JobRecord(item["sk"], item["kind"], item["run_id"], item.get("label", ""), item["status"],
                     item.get("exit_code"), item.get("finished_at"), bool(item.get("dismissed")),
                     json.loads(item.get("prompt_ids") or "null"), item.get("message"), store)


def finish(store, job_id: str, status: str, exit_code: int | None, message: str | None, now: float,
           actual: float | None = None) -> bool:
    """Mark a job done/failed/interrupted exactly once, settling its spend in the same transaction (its reservation
    is replaced by `actual`, or kept as the charge when the cost is unknown), then release its slot and evaluation
    lock. False if someone (its worker or a sweep) already finished it."""
    item = store.get_item(PK, job_id) or {}
    reserved = float(item.get("reservation") or 0.0)
    charge = reserved if actual is None else actual
    actions = [Update(PK, job_id, set={"status": status, "exit_code": exit_code, "message": (message or "")[:300],
                                       "finished_at": now, "charged": charge},
                      cond=Cond.isin("status", ["queued", "running"]))]
    if item.get("month") and (reserved or charge):
        # Settled on the month whose cap allowed the job (its start month), so a job crossing a month boundary
        # can't free headroom in the new month.
        actions.append(Update("SPEND", item["month"], add={"committed": round(charge - reserved, 6)}))
    try:
        store.transact(actions, token=f"fin-{secrets.token_hex(8)}")  # unique per call: one call's retries share it
    except Conflict:
        return False
    for pk, sk, attr in (("SLOT", item.get("slot") or "", "job_id"), ("LOCK", item.get("eval_lock") or "", "owner")):
        if sk:
            try:
                store.write(Delete(pk, sk, cond=Cond.eq(attr, job_id)))
            except Conflict:
                pass  # a newer job holds it now
    return True


class CloudBackend:
    def __init__(self, store, invoke=None, clock=time.time):
        self.store, self.invoke, self.clock = store, invoke, clock

    # --- starting ------------------------------------------------------------------------------------------------

    def start(self, ws: Workspace, kind: str, run_id: str, label: str, args: list[str], *, singleton: bool,
              prompt_ids: list[str], evaluate_dir: Path | None, quiet: bool, task: str | None,
              reservation: float = 0.0):
        now = self.clock()
        job_id = secrets.token_hex(8)
        deadline = now + EVENT_AGE_S + RUN_LIMIT_S + MARGIN_S if kind != "batch" else FAR
        eval_lock = f"eval#{evaluate_dir.name}" if evaluate_dir is not None else ""
        item = {"pk": PK, "sk": job_id, "kind": kind, "run_id": run_id, "label": label, "status": "queued",
                "args": json.dumps(args), "prompt_ids": json.dumps(prompt_ids or None), "task": task or "",
                "queued_at": now, "deadline": deadline, "dismissed": False, "slot": kind if singleton else "",
                "eval_lock": eval_lock}
        capped = ws.cap_usd is not None and reservation > 0
        if capped:
            if reservation > ws.cap_usd:
                raise spend.refusal(self._committed(now), ws.cap_usd, now)
            item.update(reservation=reservation, month=spend.month(now))
        actions = [Put(item, cond=Cond.absent())]
        if capped:  # a condition can't do arithmetic: the app supplies the threshold cap − reservation (ADR-8)
            actions.append(Update("SPEND", item["month"], add={"committed": reservation},
                                  cond=Cond.any(Cond.missing("committed"),
                                                Cond.le("committed", round(ws.cap_usd - reservation, 6)))))
        if singleton:
            actions.append(Put({"pk": "SLOT", "sk": kind, "job_id": job_id, "run_id": run_id, "expires": deadline},
                               cond=Cond.any(Cond.absent(), Cond.lt("expires", now))))
        if eval_lock:
            actions.append(Put({"pk": "LOCK", "sk": eval_lock, "owner": job_id, "expires": now + EVENT_AGE_S + RUN_LIMIT_S
                                + MARGIN_S}, cond=Cond.any(Cond.absent(), Cond.lt("expires", now))))
        if task:
            actions.append(Check("LOCK", lease_key(task), Cond.any(Cond.absent(), Cond.lt("expires", now))))
        try:
            self.store.transact(actions, token=f"start-{job_id}")
        except Conflict as exc:
            failed = [type(a).__name__ + ":" + (a.item["pk"] if isinstance(a, Put) else a.pk)
                      for a, r in zip(actions, exc.reasons) if r == "ConditionalCheckFailed"]
            if any(f.endswith(":SLOT") for f in failed):
                if quiet:
                    return None
                holder = self.store.get_item("SLOT", kind) or {}
                raise ServiceError(409, BUSY[kind].format(holder.get("run_id", "?"))) from None
            if any(f == "Put:LOCK" for f in failed):
                raise ServiceError(409, f"Run {run_id} is already being scored.") from None
            if any(f == "Check:LOCK" for f in failed):
                raise ServiceError(409, "This task's prompts are being edited; try again in a moment.") from None
            if any(f == "Update:SPEND" for f in failed):
                raise spend.refusal(self._committed(now), ws.cap_usd, now) from None
            raise ServiceError(409, "Busy; try again") from None
        try:
            if self.invoke is not None:
                self.invoke(job_id)
        except Exception as exc:  # the worker never got it: finish now so nothing stays reserved
            finish(self.store, job_id, "failed", 2, f"could not start the worker: {type(exc).__name__}", self.clock())
            raise ServiceError(500, "Could not start the job; try again") from None
        return record(item, self.store)

    def _committed(self, now: float) -> float:
        item = self.store.get_item("SPEND", spend.month(now)) or {}
        return float(item.get("committed") or 0.0)

    # --- reading ------------------------------------------------------------------------------------------------

    def all(self, ws: Workspace) -> list[JobRecord]:  # noqa: ARG002
        self.sweep()
        items = self.store.query(PK)
        return [record(i, self.store) for i in sorted(items, key=lambda i: i.get("queued_at", 0))]

    def sweep(self) -> int:
        """Finish (as interrupted) every queued or running job past its deadline. Returns how many it finished."""
        now, n = self.clock(), 0
        for it in self.store.query(PK):
            if it.get("status") in ("queued", "running") and it.get("deadline", FAR) < now:
                n += finish(self.store, it["sk"], "interrupted", None,
                            "interrupted: the worker stopped before finishing (timeout or crash)", now)
        return n

    def is_evaluating(self, ws: Workspace, run_dir: Path) -> bool:  # noqa: ARG002
        lock = self.store.get_item("LOCK", f"eval#{run_dir.name}")
        return bool(lock) and lock.get("expires", 0) > self.clock()

    def dismiss(self, ws: Workspace, job) -> None:  # noqa: ARG002
        self.store.write(Update(PK, job.id, set={"dismissed": True}))

    # --- the per-task lease (prompt edits vs run starts) ----------------------------------------------------------

    @contextmanager
    def exclusive(self, ws: Workspace, task: str):  # noqa: ARG002
        owner = secrets.token_hex(8)
        key = lease_key(task)
        for _ in range(40):
            now = self.clock()
            try:
                self.store.write(Put({"pk": "LOCK", "sk": key, "owner": owner, "expires": now + LEASE_S},
                                     cond=Cond.any(Cond.absent(), Cond.lt("expires", now))))
                break
            except Conflict:
                time.sleep(0.25)
        else:
            raise ServiceError(409, "This task is busy; try again in a moment.")
        try:
            yield owner
        finally:
            try:
                self.store.write(Delete("LOCK", key, cond=Cond.eq("owner", owner)))
            except Conflict:
                pass
