"""Task packs under tasks/<task_id>/: prompts.jsonl, rubric.json, system.txt, and optional expected.jsonl."""

from __future__ import annotations

import hashlib
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
    archived: bool = False  # hidden from the UI's pickers (increment 13); the CLI still accepts it


def task_dir(task_id: str) -> Path:
    return project_root() / "tasks" / task_id


def load_prompts(task_id: str) -> list[Prompt]:
    path = task_dir(task_id) / "prompts.jsonl"
    if not path.exists():
        raise ConfigError(path, "(file)", f"no prompts for task '{task_id}'")
    return parse_prompts(path)


def parse_prompts(path: Path) -> list[Prompt]:
    """Every prompt line of a prompts.jsonl (the UI reads its own root's file through this; increment 13)."""
    prompts = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
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
        prompts.append(Prompt(pid, text, obj.get("archived") is True))
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


TOOL_RE = re.compile(r"^([A-Za-z0-9._-]{1,64}/)?[A-Za-z0-9._-]{1,128}$")


@dataclass(frozen=True)
class Expectation:
    """Expected workflow for one prompt: ordered stages, each a list of acceptable tools ("name" or "server/name")."""
    stages: list[list[str]]
    sha256: str  # of the whole expected.jsonl, for provenance


def load_expected(task_id: str, prompt_id: str) -> Expectation | None:
    """The expectation for (task, prompt), or None when the task has no expected.jsonl or no line for the prompt."""
    return parse_expected(task_dir(task_id) / "expected.jsonl").get(prompt_id)


def parse_expected(path: Path) -> dict[str, Expectation]:
    """Every prompt's expectation in an expected.jsonl ({} when there is no file); a later line for an id wins."""
    if not path.exists():
        return {}
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    found: dict[str, Expectation] = {}
    for lineno, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            pid, stages = obj["prompt_id"], obj["stages"]
        except (json.JSONDecodeError, KeyError, TypeError):
            raise RubricError(f"{path}:{lineno}: each line must be {{\"prompt_id\": ..., \"stages\": [[tool, ...], ...]}}") from None
        if not isinstance(pid, str) or not PROMPT_ID_RE.match(pid):
            raise RubricError(f"{path}:{lineno}: prompt_id {pid!r} must match ^[A-Za-z0-9._-]{{1,64}}$")
        if (not isinstance(stages, list) or not stages
                or not all(isinstance(s, list) and s and all(isinstance(t, str) and TOOL_RE.match(t) for t in s)
                           for s in stages)):
            raise RubricError(f"{path}:{lineno}: stages must be a non-empty list of non-empty lists of tool names "
                              '("name" or "server/name")')
        found[pid] = Expectation(stages, sha)
    return found


def scorer_path(task_id: str) -> Path:
    return task_dir(task_id) / "scorer.json"


def load_scorer(task_id: str) -> dict | None:
    """The task's output-scorer spec (requirement-gaps ADR-1): None when the task has no scorer.json, which means the
    judge scores the output. A malformed file raises RubricError naming the file."""
    path = scorer_path(task_id)
    if not path.is_file():
        return None
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RubricError(f"{path}: not valid JSON ({exc})") from None
    if not isinstance(spec, dict) or not isinstance(spec.get("kind"), str):
        raise RubricError(f'{path}: must be an object with a "kind"')
    return spec
