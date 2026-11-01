"""One API test, both setups (backend-parity design ADR-6): the `backend` fixture yields a Harness for the laptop app
and for the cloud app, so a service that forgets the cloud fails here, not after a deploy.

The cloud Harness is the real app on a FakeStore with the real Mirror and CloudBackend. Jobs run with the real worker
code (`workers.run_job`, `batch_prepare`/`batch_item`/`batch_finalize`) and real `foundry` subprocesses, on the
worker's own workspace. Before every request its API copy is emptied, as a cold Lambda's /tmp would be, so a read
that skips `state.of(ws).fresh_*()` sees nothing.

Tests stage run dirs in `h.runs_dir` and project files in `h.project`, then call `h.sync()` / `h.sync_project()`
(no-ops on the laptop, where those are the app's own dirs); `h.pull(run_id)` / `h.pull_project()` bring back what the
API or a worker wrote.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from conftest import FIXTURES, ROOT
from fakestore import FakeStore
from fastapi.testclient import TestClient

from foundry import cloud_boot
from foundry.data.mirror import Mirror
from foundry.server import create_app
from foundry.services import jobs, workers
from foundry.services.cloud_jobs import CloudBackend
from foundry.services.cloud_state import CloudState
from foundry.services.workspace import Workspace

BASE = "http://127.0.0.1:8765"


def _project(dest: Path, config_from: Path | None = None) -> Path:
    """A project root: tasks/ copied, config/ copied (or linked to `config_from`), fixtures/ linked."""
    dest.mkdir(parents=True, exist_ok=True)
    if config_from is None:
        shutil.copytree(ROOT / "tasks", dest / "tasks", ignore=shutil.ignore_patterns("drafts"))
        shutil.copytree(ROOT / "config", dest / "config")
    else:
        (dest / "tasks").mkdir(exist_ok=True)
        (dest / "config").symlink_to(config_from)
    (dest / "fixtures").symlink_to(ROOT / "fixtures")
    return dest


class Harness:
    name = ""
    client: TestClient
    csrf: str
    runs_dir: Path  # where tests stage run dirs
    project: Path  # where tests stage project files (tasks/, config/)

    def headers(self, extra: dict | None = None) -> dict:
        return {"x-csrf-token": self.csrf, **(extra or {})}

    def seed(self, fixture: str, run_id: str | None = None) -> Path:
        dest = self.runs_dir / (run_id or f"fixture-{fixture}")
        shutil.copytree(FIXTURES / fixture, dest)
        self.sync()
        return dest

    def sync(self) -> None: ...
    def sync_project(self) -> None: ...
    def pull(self, run_id: str) -> Path: return self.runs_dir / run_id
    def pull_project(self) -> None: ...
    def drain(self, timeout: float = 120) -> None: ...


class LaptopHarness(Harness):
    name = "laptop"

    def __init__(self, tmp: Path):
        self.project = _project(tmp / "proj")
        self.runs_dir = (tmp / "runs").resolve()
        self.app = create_app(self.runs_dir, self.project, port=8765)
        self.ws = self.app.state.ui
        self.client = TestClient(self.app, base_url=BASE, follow_redirects=False)
        self.csrf = self.client.get("/api/session").json()["csrf"]

    def drain(self, timeout: float = 120) -> None:
        deadline = time.monotonic() + timeout  # a real process: poll it, bounded
        while jobs.live(self.ws):
            assert time.monotonic() < deadline, f"jobs still running after {timeout} s"
            time.sleep(0.1)


class ColdClient(TestClient):
    """Empties what the API's mirror owns before every request: each request starts as a cold instance."""

    def __init__(self, app, mirror: Mirror, root: Path, **kw):
        super().__init__(app, **kw)
        self._mirror, self._root = mirror, root

    def request(self, *args, **kw):
        self._mirror.seen.clear()
        for owned in (self._root / "runs", self._root / "project" / "tasks", self._root / "files"):
            if owned.is_dir():
                for p in owned.iterdir():
                    shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
        return super().request(*args, **kw)


class CloudHarness(Harness):
    name = "cloud"

    def __init__(self, tmp: Path, monkeypatch):
        tmp = tmp.resolve()
        self.store, self.queue = FakeStore(), []
        self._monkeypatch = monkeypatch
        # staging: where tests put runs and project files, pushed like any other writer
        self.stage = tmp / "stage"
        self.project = _project(self.stage / "project")
        self.runs_dir = self.stage / "runs"
        self.runs_dir.mkdir(parents=True)
        self.smirror = Mirror(self.store, self.stage)
        cloud_boot.seed_projects(self.store, self.smirror, app=self.project)
        self.sws = self._workspace(self.stage, self.smirror)
        # the API instance: config/ is the image's (here: the staging project's), tasks/ come from the store
        api = tmp / "api"
        _project(api / "project", config_from=self.project / "config")
        self.amirror = Mirror(self.store, api)
        for it in self.store.query("TREE", prefix="project#"):  # a cold start pulls the projects (cloud_boot)
            self.amirror.pull(it["sk"], item=it)
        backend = CloudBackend(self.store, invoke=self.queue.append,
                               start_batch=lambda job_id: self.queue.append(job_id) or f"arn:test:{job_id}")
        self.app = create_app(api / "runs", api / "project", cloud=(self.store, self.amirror, backend))
        self.ws = self.app.state.ui
        self.client = ColdClient(self.app, self.amirror, api, base_url=BASE, follow_redirects=False)
        self.csrf = self.client.get("/api/session").json()["csrf"]
        # the worker
        wroot = tmp / "worker"
        _project(wroot / "project", config_from=self.project / "config")
        (wroot / "runs").mkdir()
        self.wmirror = Mirror(self.store, wroot)
        self.wws = self._workspace(wroot, self.wmirror)

    def _workspace(self, root: Path, mirror: Mirror) -> Workspace:
        ws = Workspace(root / "runs", root / "project", 0, backend=CloudBackend(self.store))
        ws.cloud, ws.state = (self.store, mirror), CloudState(self.store, mirror)
        return ws

    def _rebase(self, mirror: Mirror, tree: str) -> None:
        """Point the mirror's last-seen version at the store's, keeping the local files: the next push diffs the
        test's staged files against what's stored now (an API or a worker may have written meanwhile)."""
        version, manifest, item = mirror.read(tree)
        if item is not None:
            mirror.seen[tree] = (version, manifest)

    def sync(self) -> None:
        for d in sorted(p for p in self.runs_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            self._rebase(self.smirror, f"run#{d.name}")
            workers.index_run(self.sws, self.smirror, d.name)
        batches = self.runs_dir / ".batches"
        for d in sorted(p for p in batches.iterdir() if p.is_dir()) if batches.is_dir() else []:
            self._rebase(self.smirror, f"batch#{d.name}")
            self.smirror.push_dir(f"batch#{d.name}")

    def sync_project(self) -> None:
        for d in sorted(p for p in (self.project / "tasks").iterdir() if p.is_dir()):
            self._rebase(self.smirror, f"project#{d.name}")
            self.smirror.push_dir(f"project#{d.name}")

    def pull(self, run_id: str) -> Path:
        self.smirror.pull(f"run#{run_id}")
        return self.runs_dir / run_id

    def pull_project(self) -> None:
        for it in self.store.query("TREE", prefix="project#"):
            self.smirror.pull(it["sk"], item=it)

    def drain(self, timeout: float = 120) -> None:
        deadline = time.monotonic() + timeout
        with self._monkeypatch.context() as m:
            m.setenv("FOUNDRY_HOME", str(self.wws.root))  # as on Lambda: the worker's project is the mirrored one
            while self.queue:
                assert time.monotonic() < deadline, f"jobs still queued after {timeout} s"
                job_id = self.queue.pop(0)
                job = self.store.get_item("JOB", job_id) or {}
                if job.get("kind") == "batch":
                    out = workers.batch_prepare(self.wws, self.store, self.wmirror, job_id)
                    for item in out["items"]:
                        workers.batch_item(self.wws, self.store, self.wmirror, item)
                    workers.batch_finalize(self.wws, self.store, self.wmirror, job_id)
                else:
                    workers.run_job(self.wws, self.store, self.wmirror, job_id)


def make(request, tmp_path, monkeypatch) -> Harness:
    """The `backend` fixture's body (conftest.py): the app on each setup; tests marked laptop_only / cloud_only run
    on that setup alone."""
    for setup in ("laptop", "cloud"):
        marker = request.node.get_closest_marker(f"{setup}_only")
        if marker is not None and request.param != setup:
            pytest.skip(f"{setup} only: {marker.args[0] if marker.args else ''}")
    monkeypatch.delenv("FOUNDRY_HOME", raising=False)
    return LaptopHarness(tmp_path) if request.param == "laptop" else CloudHarness(tmp_path, monkeypatch)
