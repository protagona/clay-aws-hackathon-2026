"""Every kind of background job, declared once (backend-parity design ADR-4): the laptop's job backend, the cloud's,
and the cloud worker all read what a kind is from here instead of `if kind == ...` chains.

A new kind of job: add a `Kind` to KINDS, a `foundry` subcommand that does the work, and a service that calls
`jobs.start(ws, "<name>", ...)`. Both setups then run it (tests/test_backends.py checks every kind used is here).
Worker events (healthz, batch steps, reindex) are a different thing: lambda_worker.EVENTS.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Kind:
    name: str
    singleton: bool  # one live at a time (the 409 says `busy`)
    ordinary_exit_1: bool  # exit 1 is an ordinary failure with its own page (a failed run), not a banner
    long_running: bool  # no job deadline: its workflow releases it (the cloud runs it on Step Functions)
    busy: str | None  # the 409 while one is live; "{}" is the live one's run id
    syncs_run: bool  # the cloud worker pushes the run while it runs and at the end
    reads_run: bool  # the cloud worker pulls the run first and records it after
    output_file: str | None  # a file the cloud worker publishes for every instance (file#tools)
    cost: str  # how the cloud worker reads what it cost: "run" | "evaluate" | "zero" | "booked" (by the workflow)
    startable: bool = True  # False: a spend reservation only, for work done in the request itself


RUN = Kind("run", singleton=True, ordinary_exit_1=True, long_running=False, busy="A run is already in progress: {}",
           syncs_run=True, reads_run=False, output_file=None, cost="run")
EVALUATE = Kind("evaluate", singleton=False, ordinary_exit_1=False, long_running=False, busy=None,
                syncs_run=False, reads_run=True, output_file=None, cost="evaluate")
BATCH = Kind("batch", singleton=True, ordinary_exit_1=True, long_running=True, busy="A batch is already running: {}",
             syncs_run=False, reads_run=False, output_file=None, cost="booked")
TOOLS = Kind("tools", singleton=True, ordinary_exit_1=False, long_running=False,
             busy="The tool list is already being refreshed", syncs_run=False, reads_run=False,
             output_file="ui-tools.json", cost="zero")
DRAFT = Kind("draft", singleton=False, ordinary_exit_1=False, long_running=False, busy=None, syncs_run=False,
             reads_run=False, output_file=None, cost="booked", startable=False)  # a rubric draft, in the request

KINDS: dict[str, Kind] = {k.name: k for k in (RUN, EVALUATE, BATCH, TOOLS, DRAFT)}


def kind(name: str) -> Kind:
    try:
        return KINDS[name]
    except KeyError:
        raise ValueError(f"unknown job kind: {name!r} (declare it in services/job_kinds.py)") from None


def failed_visibly(kind_name: str, exit_code: int | None, status: str | None, alive: bool, dismissed: bool) -> bool:
    """Whether a finished job needs a banner. A failed run or batch (exit 1) is shown on its own page; exit 2 means
    nothing was created. An interrupted job always needs one. A kind this code doesn't know (an older record) is
    treated like any other job."""
    if alive or dismissed:
        return False
    if status == "interrupted":
        return True
    k = KINDS.get(kind_name)
    if k is not None and k.ordinary_exit_1:
        return exit_code == 2
    return exit_code not in (0, None)
