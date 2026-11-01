"""The cloud's state (aws-deploy design ADR-5, backend-parity ADR-1): an instance's /tmp files are a copy of the
store's trees, so reads bring the copy current first and writes are pushed. Services reach this only through
`state.of(ws)`; this is one of the backend modules (tests/test_architecture.py) allowed to touch the store.

`fresh_run` pulls one run, `fresh_runs` all of them, `fresh_project(s)` the prompts and rubrics, `fresh_batches` the
batch trees, `fresh_tools` the tool listing. `project_write` holds the task's lease, pulls, checks the version the page
loaded, and pushes what changed (fenced by the lease). `record_run` pushes a run with its board card and summary (the
run index the board reads); `refresh_row` rewrites only the stored card and summary (re-indexing).
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from foundry.data import index, tool_cache
from foundry.data import runs as data_runs
from foundry.data import uploads as data_uploads
from foundry.data.mirror import PK as TREE
from foundry.data.store import Conflict, Cond, Put, Update
from foundry.services import cards, jobs, state
from foundry.services.errors import ServiceError
from foundry.services.workspace import Workspace

_held = threading.local()  # (ws id, task) -> lease owner, so a write inside a write doesn't wait on itself

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


class CloudState:
    cards_fresh = False  # board cards come from the run index, written when a run was last recorded

    def __init__(self, store, mirror):
        self.store, self.mirror = store, mirror

    # --- reads ----------------------------------------------------------------------------------------------------

    def fresh_run(self, ws: Workspace, run_id: str) -> None:  # noqa: ARG002
        item = self.store.get_item(TREE, f"run#{run_id}")
        if item is not None:
            self.mirror.pull(f"run#{run_id}", item=item)

    def fresh_runs(self, ws: Workspace) -> None:  # noqa: ARG002
        pull_all(self.mirror, self.store.query(TREE, prefix="run#"))

    def fresh_project(self, ws: Workspace, task: str) -> None:
        if (id(ws), task) not in getattr(_held, "owners", {}):  # inside a write the pull already happened
            self.mirror.pull(f"project#{task}")

    def fresh_projects(self, ws: Workspace) -> None:
        held = getattr(_held, "owners", {})
        for it in self.store.query(TREE, prefix="project#"):
            if (id(ws), it["sk"].partition("#")[2]) not in held:
                self.mirror.pull(it["sk"], item=it)

    def fresh_batches(self, ws: Workspace) -> None:  # noqa: ARG002
        pull_all(self.mirror, self.store.query(TREE, prefix="batch#"))

    def fresh_tools(self, ws: Workspace) -> None:
        self.mirror.pull("file#tools")
        tool_cache.install(self.mirror.local_dir("file#tools") / "ui-tools.json", ws.runs_dir)

    def project_version(self, ws: Workspace, task: str) -> int | None:  # noqa: ARG002
        """The version of a task's project files this instance last pulled, for pages to send back."""
        return self.mirror.seen.get(f"project#{task}", (None, None))[0]

    # --- project writes -------------------------------------------------------------------------------------------

    @contextmanager
    def project_write(self, ws: Workspace, task: str, hold_starts: bool = False):  # noqa: ARG002 (always held here)
        """Under the task's lease (which also holds off run starts of the task), on a fresh pull, with the changes
        pushed at the end; a concurrent writer that got in first, or a page showing an older version, is a 409."""
        key = (id(ws), task)
        held = getattr(_held, "owners", {})
        if key in held:  # already inside this task's write (a store call within set_text's)
            yield
            return
        with jobs.exclusive(ws, task) as owner:
            _held.owners = {**held, key: owner}
            try:
                version = self.mirror.pull(f"project#{task}")
                expected = state.expected_version()
                if expected is not None and expected != version:
                    raise ServiceError(409, "Changed by someone else; reload")
                yield
                try:
                    self.mirror.push_dir(f"project#{task}", lease=(f"lease#project#{task}", owner))
                except Conflict:
                    raise ServiceError(409, "Changed by someone else; reload") from None
            finally:
                _held.owners = {k: v for k, v in _held.owners.items() if k != key}

    # --- runs -----------------------------------------------------------------------------------------------------

    def record_run(self, ws: Workspace, run_id: str) -> bool:
        """Push the run's changed files together with its board card and summary (the run index)."""
        d = ws.runs_dir / run_id
        row = cards.board_row(ws, d)
        attrs = index.run_attrs(row["card"], row["summary"]) if row["summary"] is not None else None
        self.mirror.push_dir(f"run#{run_id}", attrs=attrs)
        return True

    def refresh_row(self, ws: Workspace, run_id: str) -> str:
        """Re-index one pulled run: rewrite its stored card and summary if they differ from what its files say now,
        without a new version (a worker that pulled it earlier can still push). "same", "updated" or "busy"."""
        tree = f"run#{run_id}"
        item = self.store.get_item(TREE, tree)
        if item is None:
            return "same"
        version = self.mirror.pull(tree, item=item)
        d = data_runs.safe_run_path(ws.runs_dir, run_id) or ws.runs_dir / run_id
        row = cards.board_row(ws, d)
        new = index.run_attrs(row["card"], row["summary"]) if row["summary"] is not None else {
            "card": json.dumps(row["card"], sort_keys=True), "summary": "null"}
        if {k: item.get(k) for k in new} == new:
            return "same"
        try:
            self.store.write(Update(TREE, tree, set=new, cond=Cond.eq("version", version)))
        except Conflict:
            return "busy"
        return "updated"

    def board_rows(self, ws: Workspace) -> list[dict]:  # noqa: ARG002
        return [{"run_id": it["run_id"], "card": it["card"], "summary": it["summary"]}
                for it in index.run_items(self.store)]

    def evaluating_runs(self, ws: Workspace) -> set[str]:  # noqa: ARG002
        now = time.time()
        return {it["sk"].partition("#")[2] for it in self.store.query("LOCK", prefix="eval#")
                if it.get("expires", 0) > now}

    # --- uploads (aws-deploy design ADR-10: a presigned S3 POST; a function URL takes at most 6 MB a request) --------

    def upload_ticket(self, ws: Workspace, upload_id: str, meta: dict) -> dict:  # noqa: ARG002
        self.store.write(Put({"pk": "UPLOAD", "sk": upload_id, **meta, "ttl": int(time.time()) + 86400}))
        post = self.store.presign_post(f"uploads/{upload_id}", meta["max_bytes"])
        return {"upload_id": upload_id, "url": post["url"], "fields": post["fields"]}

    def upload_local_target(self, ws: Workspace, upload_id: str) -> tuple[Path, int]:  # noqa: ARG002
        raise ServiceError(404, "Unknown upload")  # files go straight to S3 here

    def upload_take(self, ws: Workspace, upload_id: str, kind: str) -> tuple[str, Path]:
        meta = self.store.get_item("UPLOAD", upload_id)
        if meta is None or meta.get("kind") != kind:
            raise ServiceError(404, "Unknown upload")
        size = self.store.object_size(f"uploads/{upload_id}")
        if size is None:
            raise ServiceError(400, "The upload hasn't arrived; send the file first.")
        if size > int(meta["max_bytes"]):
            raise ServiceError(413, "The file is over the limit.")
        return meta["filename"], data_uploads.save_bytes(ws.runs_dir, upload_id, self.store.get_object(f"uploads/{upload_id}"))
