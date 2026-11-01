"""Uploaded files waiting to be imported or drafted from (aws-deploy design ADR-10).

Locally a file lands in <runs dir>/.uploads/<id> with <id>.meta.json beside it. In the cloud it lands in the
state bucket (uploads/<id>, through a presigned POST) and an instance copies it here before reading it."""

from __future__ import annotations

import json
import re
from pathlib import Path

UPLOAD_ID_RE = re.compile(r"^[a-f0-9]{16}$")


def path(runs_dir: Path, upload_id: str) -> Path:
    return runs_dir / ".uploads" / upload_id


def write_meta(runs_dir: Path, upload_id: str, meta: dict) -> None:
    d = runs_dir / ".uploads"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{upload_id}.meta.json").write_text(json.dumps(meta))


def read_meta(runs_dir: Path, upload_id: str) -> dict | None:
    try:
        return json.loads((runs_dir / ".uploads" / f"{upload_id}.meta.json").read_text())
    except (OSError, ValueError):
        return None


def save_bytes(runs_dir: Path, upload_id: str, data: bytes) -> Path:
    p = path(runs_dir, upload_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def size(runs_dir: Path, upload_id: str) -> int | None:
    p = path(runs_dir, upload_id)
    return p.stat().st_size if p.is_file() and not p.is_symlink() else None


def read_bytes(p: Path) -> bytes:
    return Path(p).read_bytes()
