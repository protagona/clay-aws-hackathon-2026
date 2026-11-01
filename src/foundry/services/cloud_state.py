"""Keeping an API instance's /tmp copy of the state current in the cloud (aws-deploy design ADR-5); no-ops on a laptop.

Services call these before reading files: `pull_run` for one run, `pull_runs` for all of them (the dashboard, the
prompt pages, the tool menu, the rubric overview), `pull_projects` for prompts and rubrics, `pull_tools` for the
tool listing. Writes to a task's project files go through `project_write`, which holds the task's lease, pulls, and
pushes what changed (fenced by the lease) when the block ends.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from foundry.data import tool_cache
from foundry.data.mirror import PK as TREE
from foundry.data.store import Conflict
from foundry.services import jobs
from foundry.services.errors import ServiceError
from foundry.services.workspace import Workspace

_held = threading.local()  # (ws id, task) -> lease owner, so a write inside a write doesn't wait on itself
_expect = threading.local()  # the project version the editing page loaded (spec S2), for this request's thread


@contextmanager
def expecting(base_version: int | None):
    """Writes inside this block refuse (409) if the task's project files moved past `base_version`, the version
    the page was showing. None: no check (scripts and the CLI that don't send one)."""
    prev = getattr(_expect, "version", None)
    _expect.version = base_version
    try:
        yield
    finally:
        _expect.version = prev


def project_version(ws: Workspace, task: str) -> int | None:
    """The version of a task's project files this instance last pulled (cloud), for pages to send back as
    `base_version`; None on a laptop."""
    if not on(ws):
        return None
    _, mirror = ws.cloud
    return mirror.seen.get(f"project#{task}", (None, None))[0]


def on(ws: Workspace) -> bool:
    return ws.cloud is not None


def pull_run(ws: Workspace, run_id: str) -> None:
    if on(ws):
        store, mirror = ws.cloud
        item = store.get_item(TREE, f"run#{run_id}")
        if item is not None:
            mirror.pull(f"run#{run_id}", item=item)


PULL_THREADS = 16  # a cold instance pulls every run once: ~900 small GETs took 26-34 s one at a time (slice 10)


def pull_all(mirror, items: list[dict]) -> None:
    """Pull many trees at once: each tree is its own directory and its own `seen` entry, and boto3 clients are
    thread-safe."""
    if len(items) < 2:
        for it in items:
            mirror.pull(it["sk"], item=it)
        return
    with ThreadPoolExecutor(PULL_THREADS) as pool:
        list(pool.map(lambda it: mirror.pull(it["sk"], item=it), items))


def pull_runs(ws: Workspace) -> None:
    if on(ws):
        store, mirror = ws.cloud
        pull_all(mirror, store.query(TREE, prefix="run#"))


def pull_project(ws: Workspace, task: str) -> None:
    if on(ws) and (id(ws), task) not in getattr(_held, "owners", {}):  # inside a write the pull already happened
        _, mirror = ws.cloud
        mirror.pull(f"project#{task}")


def pull_batches(ws: Workspace) -> None:
    if on(ws):
        store, mirror = ws.cloud
        pull_all(mirror, store.query(TREE, prefix="batch#"))


def pull_projects(ws: Workspace) -> None:
    if on(ws):
        store, mirror = ws.cloud
        for it in store.query(TREE, prefix="project#"):
            mirror.pull(it["sk"], item=it)


def pull_tools(ws: Workspace) -> None:
    if on(ws):
        _, mirror = ws.cloud
        mirror.pull("file#tools")
        tool_cache.install(mirror.local_dir("file#tools") / "ui-tools.json", ws.runs_dir)


@contextmanager
def project_write(ws: Workspace, task: str, hold_starts: bool = False):
    """Change a task's project files. In the cloud: under the task's lease (which also holds off run starts of the
    task), on a fresh pull, with the changes pushed at the end; a concurrent writer that got in first makes this a
    409. Locally: just the block, or under the server lock when `hold_starts` (a prompt's text must not change while
    a run of it starts), as before; never around slow work like rubric drafting."""
    key = (id(ws), task)
    held = getattr(_held, "owners", {})
    if key in held:  # already inside this task's write (a store call within set_text's)
        yield
        return
    if not on(ws) and not hold_starts:
        yield
        return
    with jobs.exclusive(ws, task) as owner:  # locally the server lock; in the cloud the task's lease
        _held.owners = {**held, key: owner}
        try:
            if not on(ws):
                yield
                return
            _, mirror = ws.cloud
            version = mirror.pull(f"project#{task}")
            expected = getattr(_expect, "version", None)
            if expected is not None and expected != version:
                raise ServiceError(409, "Changed by someone else; reload")
            yield
            try:
                mirror.push_dir(f"project#{task}", lease=(f"lease#project#{task}", owner))
            except Conflict:
                raise ServiceError(409, "Changed by someone else; reload") from None
        finally:
            _held.owners = {k: v for k, v in _held.owners.items() if k != key}


def evaluating_runs(ws: Workspace) -> set[str]:
    """Run ids with a live evaluation lock (one query; the board's overlay)."""
    store, _ = ws.cloud
    import time

    now = time.time()
    return {it["sk"].partition("#")[2] for it in store.query("LOCK", prefix="eval#") if it.get("expires", 0) > now}
