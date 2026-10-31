"""The project's own files: config stems, task ids, the active rubric's hash (increment 16, from ui/app.py)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from foundry.config import ConfigError
from foundry.data.runs import is_regular
from foundry.data.tasks import parse_prompts

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


def config_exists(root: Path, stem: str) -> bool:
    return bool(CONFIG_STEM_RE.match(stem)) and config_path(root, stem).is_file()


def task_prompts(root: Path, task: str) -> list:
    """Every prompt of a task (archived ones included). Raises ConfigError when the task has no prompts file or it
    is malformed."""
    path = root / "tasks" / task / "prompts.jsonl"
    if not path.is_file():
        raise ConfigError(f"tasks/{task}/prompts.jsonl", "(file)", f"no prompts for task '{task}'")
    return parse_prompts(path)


def script_exists(root: Path, script: str) -> bool:
    """A scripted (offline) model's script, relative to the project root unless absolute."""
    p = Path(script)
    return (p if p.is_absolute() else root / p).is_file()


def task_dir(root: Path, task: str) -> Path:
    return root / "tasks" / task
