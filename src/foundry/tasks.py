"""Task packs under tasks/<task_id>/: prompts.jsonl, rubric.json, system.txt."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from foundry.config import ConfigError, project_root
from foundry.contract import Rubric

PROMPT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True)
class Prompt:
    prompt_id: str
    prompt: str


def task_dir(task_id: str) -> Path:
    return project_root() / "tasks" / task_id


def load_prompts(task_id: str) -> list[Prompt]:
    path = task_dir(task_id) / "prompts.jsonl"
    if not path.exists():
        raise ConfigError(path, "(file)", f"no prompts for task '{task_id}'")
    prompts = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            pid, text = obj["prompt_id"], obj["prompt"]
        except (json.JSONDecodeError, KeyError, TypeError):
            raise ConfigError(f"{path}:{lineno}", "(line)", 'each line must be {"prompt_id": ..., "prompt": ...}') from None
        if not isinstance(pid, str) or not PROMPT_ID_RE.match(pid) or ".." in pid:
            raise ConfigError(f"{path}:{lineno}", "prompt_id",
                              f"{pid!r} must match ^[A-Za-z0-9._-]{{1,64}}$ (it becomes part of the run id and path)")
        prompts.append(Prompt(pid, text))
    return prompts


def resolve_prompt(task_id: str, prompt_id: str) -> Prompt:
    prompts = load_prompts(task_id)
    for p in prompts:
        if p.prompt_id == prompt_id:
            return p
    available = ", ".join(p.prompt_id for p in prompts)
    raise UnknownPrompt(f"Unknown prompt id '{prompt_id}' for task '{task_id}'. Available: {available}")


class UnknownPrompt(Exception):
    pass


def system_prompt(task_id: str) -> str:
    path = task_dir(task_id) / "system.txt"
    return path.read_text() if path.exists() else ""


def rubric_path(task_id: str) -> Path:
    return task_dir(task_id) / "rubric.json"


class RubricError(Exception):
    pass


def load_rubric(path: Path) -> tuple[Rubric, bytes]:
    """Returns the validated rubric and its raw bytes (copied verbatim into the run dir)."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise RubricError(f"Rubric {path}: not found") from None
    try:
        return Rubric.model_validate_json(raw), raw
    except ValidationError as exc:
        err = exc.errors()[0]
        field = ".".join(str(p) for p in err["loc"]) or "(file)"
        raise RubricError(f"Rubric {path}: {field}: {err['msg'].removeprefix('Value error, ')}") from None
