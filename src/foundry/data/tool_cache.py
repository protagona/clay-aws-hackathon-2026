"""The cached tool menu: `foundry tools --out <runs dir>/.ui-tools.json` (increment 15; moved here in increment 16)."""

from __future__ import annotations

import json
from pathlib import Path

from foundry.data.runs import is_regular

NAME = ".ui-tools.json"


def path(runs_dir: Path) -> Path:
    return runs_dir / NAME


def install(src: Path, runs_dir: Path) -> None:
    """Put a listing pulled from the cloud store where `read` looks for it (aws-deploy slice 3c)."""
    try:
        data = Path(src).read_bytes()
    except FileNotFoundError:
        return
    tmp = runs_dir / f"{NAME}.tmp"
    tmp.write_bytes(data)
    tmp.replace(path(runs_dir))


def read(runs_dir: Path) -> dict | None:
    """The last listing, if it is a regular file holding the expected shape; anything else is ignored."""
    f = path(runs_dir)
    if not is_regular(f):
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("tools"), list) or not isinstance(data.get("servers"), list):
        return None
    return data
