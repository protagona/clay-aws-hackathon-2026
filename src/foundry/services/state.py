"""Where the app's state lives (backend-parity design ADR-1): services read and write runs, project files, batches,
the tool listing and uploads through `of(ws)`, never through the cloud's store or mirror, so a feature written once
works on a laptop and in AWS.

`LocalState` (here) is the laptop: state is plain files under the runs dir and the project root, always current, so
the `fresh_*` calls do nothing. `CloudState` (services/cloud_state.py) is AWS: an instance's files are a copy of the
store's, so `fresh_*` brings the copy current before a read, and writes are pushed. `server.create_app` and the
worker entry point set `ws.state`; a Workspace without one is a laptop.

Call `fresh_*` before reading what it names, write project files inside `project_write`, and call `record_run` after
writing a run in a request (imports). tests/backends.py runs every API test on both, with each cloud request starting
from an empty copy, so a missed call fails a test.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

from foundry.data import locks
from foundry.data import runs as data_runs
from foundry.data import uploads as data_uploads
from foundry.services import cards, jobs
from foundry.services.errors import ServiceError
from foundry.services.workspace import Workspace

_expect = threading.local()  # the project version the editing page loaded (aws-deploy spec S2), for this request


@contextmanager
def expecting(base_version: int | None):
    """Writes inside this block refuse (409) if the task's project files moved past `base_version`, the version the
    page was showing (the cloud checks it; a laptop's files can't move under it). None: no check."""
    prev = getattr(_expect, "version", None)
    _expect.version = base_version
    try:
        yield
    finally:
        _expect.version = prev


def expected_version() -> int | None:
    return getattr(_expect, "version", None)


class LocalState:
    """A laptop: the files are the state."""

    cards_fresh = True  # board cards are read from run.json on every request

    def fresh_run(self, ws: Workspace, run_id: str) -> None: ...
    def fresh_runs(self, ws: Workspace) -> None: ...
    def fresh_project(self, ws: Workspace, task: str) -> None: ...
    def fresh_projects(self, ws: Workspace) -> None: ...
    def fresh_batches(self, ws: Workspace) -> None: ...
    def fresh_tools(self, ws: Workspace) -> None: ...

    def project_version(self, ws: Workspace, task: str) -> int | None:  # noqa: ARG002
        return None

    @contextmanager
    def project_write(self, ws: Workspace, task: str, hold_starts: bool = False):
        """The block; under the server lock when `hold_starts` (a prompt's text must not change while a run of it
        starts). Never around slow work like rubric drafting."""
        if not hold_starts:
            yield
            return
        with jobs.exclusive(ws, task):
            yield

    def record_run(self, ws: Workspace, run_id: str) -> bool:  # noqa: ARG002
        return False

    def board_rows(self, ws: Workspace) -> list[dict]:
        return [cards.board_row(ws, d) for d in data_runs.run_dirs(ws.runs_dir)]

    def evaluating_runs(self, ws: Workspace) -> set[str]:
        return {d.name for d in data_runs.run_dirs(ws.runs_dir) if locks.is_locked(d)}

    # --- uploads (aws-deploy design ADR-10: a route on this server) ----------------------------------------------

    def upload_ticket(self, ws: Workspace, upload_id: str, meta: dict) -> dict:
        data_uploads.write_meta(ws.runs_dir, upload_id, meta)
        return {"upload_id": upload_id, "url": f"/api/uploads/{upload_id}", "fields": {}}

    def upload_local_target(self, ws: Workspace, upload_id: str) -> tuple[Path, int]:
        meta = data_uploads.read_meta(ws.runs_dir, upload_id) if data_uploads.UPLOAD_ID_RE.match(upload_id) else None
        if meta is None:
            raise ServiceError(404, "Unknown upload")
        return data_uploads.path(ws.runs_dir, upload_id), int(meta["max_bytes"])

    def upload_take(self, ws: Workspace, upload_id: str, kind: str) -> tuple[str, Path]:
        meta = data_uploads.read_meta(ws.runs_dir, upload_id)
        if meta is None or meta.get("kind") != kind or data_uploads.size(ws.runs_dir, upload_id) is None:
            raise ServiceError(404, "Unknown upload")
        return meta["filename"], data_uploads.path(ws.runs_dir, upload_id)


LOCAL = LocalState()


def of(ws: Workspace):
    """The workspace's state: its own (the cloud's, set by the entry point) or a laptop's."""
    return ws.state or LOCAL
