"""The project's own files: config stems, task ids, the active rubric's hash (increment 16, from ui/app.py)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from foundry.data.runs import is_regular

CONFIG_STEM_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def config_stems(root: Path) -> list[str]:
    return sorted(p.stem for p in (root / "config").glob("*.toml") if CONFIG_STEM_RE.match(p.stem))


def config_path(root: Path, stem: str) -> Path:
    return root / "config" / f"{stem}.toml"


def task_ids(root: Path) -> list[str]:
    return sorted(p.parent.name for p in (root / "tasks").glob("*/prompts.jsonl") if CONFIG_STEM_RE.match(p.parent.name))


def active_rubric_sha(root: Path, task: str) -> str | None:
    f = root / "tasks" / task / "rubric.json"
    return hashlib.sha256(f.read_bytes()).hexdigest() if is_regular(f) else None
