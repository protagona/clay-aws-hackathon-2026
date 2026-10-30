"""<run_dir>/.evaluating: one evaluation per run directory at a time."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from foundry.runstore import utc_now

LOCK = ".evaluating"


class LockHeld(Exception):
    pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read(run_dir: Path) -> dict | None:
    try:
        return json.loads((run_dir / LOCK).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def is_locked(run_dir: Path) -> bool:
    data = read(run_dir)
    return bool(data) and _alive(int(data.get("pid", 0)))


def acquire(run_dir: Path, owner: str = "cli") -> str:
    """Create the lock; a lock whose PID is dead is stale and replaced. No age limit: evaluations can be long."""
    path = run_dir / LOCK
    token = secrets.token_hex(16)
    payload = json.dumps({"pid": os.getpid(), "token": token, "owner": owner, "created_at": utc_now()})
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            data = read(run_dir)
            if data and _alive(int(data.get("pid", 0))):
                raise LockHeld(f"Run {run_dir.name} is already being evaluated.") from None
            path.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        return token
    raise LockHeld(f"Run {run_dir.name} is already being evaluated.")


def holds(run_dir: Path, token: str) -> bool:
    data = read(run_dir)
    return bool(data) and data.get("token") == token


def release(run_dir: Path, token: str) -> None:
    if holds(run_dir, token):
        (run_dir / LOCK).unlink(missing_ok=True)
