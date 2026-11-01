"""Cold-start wiring for the Lambda functions (aws-deploy design ADR-11): the project root under /tmp points at the
image's configs and fixtures, and the tasks come from the state store (until slice 2: from the image's defaults).

Lambda's filesystem is read-only except /tmp, so FOUNDRY_HOME is /tmp/state/project and the code-like parts of the
project (config/, fixtures/) are symlinks into the image (FOUNDRY_APP_DIR, default /app).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def project_root() -> Path:
    root = Path(os.environ.get("FOUNDRY_HOME", "/tmp/state/project"))
    app = Path(os.environ.get("FOUNDRY_APP_DIR", "/app"))
    root.mkdir(parents=True, exist_ok=True)
    for name in ("config", "fixtures"):
        link = root / name
        if not link.exists():
            link.symlink_to(app / name)
    if not (root / "tasks").exists() and not os.environ.get("STATE_BUCKET"):  # cloud: pulled from the store instead
        shutil.copytree(app / "tasks", root / "tasks")
    (root / "tasks").mkdir(exist_ok=True)
    return root


def runs_dir() -> Path:
    runs = Path(os.environ.get("FOUNDRY_RUNS_DIR", "/tmp/state/runs"))
    runs.mkdir(parents=True, exist_ok=True)
    return runs


def seed_projects(store, mirror, app: Path | None = None) -> None:
    """First deploy (aws-deploy design ADR-11): a task with no project tree in the store gets the image's default
    files as version 1. Racing cold starts are fine: only one create wins, the others pull its result."""
    from foundry.data.store import Conflict

    app = Path(app or os.environ.get("FOUNDRY_APP_DIR", "/app"))
    for task_dir in sorted(p for p in (app / "tasks").iterdir() if p.is_dir()):
        tree = f"project#{task_dir.name}"
        if store.get_item("TREE", tree) is not None:
            continue
        files = {f.relative_to(task_dir).as_posix(): f.read_bytes() for f in task_dir.rglob("*")
                 if f.is_file() and not any(part.startswith(".") for part in f.relative_to(task_dir).parts)}
        try:
            mirror.seen.pop(tree, None)
            mirror.push(tree, files)
        except Conflict:
            pass


def cloud_context():
    """(store, mirror, job backend) from the function's environment, with the project files seeded and pulled."""
    from foundry.data.mirror import Mirror
    from foundry.data.store import AwsStore, LambdaInvoker
    from foundry.services.cloud_jobs import CloudBackend

    store = AwsStore(os.environ["STATE_BUCKET"], os.environ["TABLE"])
    mirror = Mirror(store, runs_dir().parent)
    project_root()
    seed_projects(store, mirror)
    for it in store.query("TREE", prefix="project#"):
        mirror.pull(it["sk"], item=it)
    invoke = LambdaInvoker(os.environ["WORKER_FUNCTION"]) if os.environ.get("WORKER_FUNCTION") else None
    return store, mirror, CloudBackend(store, invoke=invoke)
