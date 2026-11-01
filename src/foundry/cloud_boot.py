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
    if not (root / "tasks").exists():  # slice 2 replaces this with a pull of the project trees from the state store
        shutil.copytree(app / "tasks", root / "tasks")
    return root


def runs_dir() -> Path:
    runs = Path(os.environ.get("FOUNDRY_RUNS_DIR", "/tmp/state/runs"))
    runs.mkdir(parents=True, exist_ok=True)
    return runs
