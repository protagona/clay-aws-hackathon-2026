"""Run directories on disk: ids, single-line JSON records, append-only trace, atomic multi-file writes."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from foundry.contract import Output, RunRecord, Step

RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def new_run_id(prompt_id: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{stamp}_{prompt_id}_{secrets.token_hex(2)}"


def is_valid_run_id(run_id: str) -> bool:
    return bool(RUN_ID_RE.match(run_id)) and ".." not in run_id


def dump_line(model_or_dict) -> str:
    """Single-line JSON (the contract wants run.json, scores.json and trace lines loadable as JSON Lines)."""
    data = model_or_dict.model_dump(exclude_none=True) if hasattr(model_or_dict, "model_dump") else model_or_dict
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class RunDir:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @classmethod
    def create(cls, runs_dir: str | Path, run_id: str) -> RunDir:
        path = Path(runs_dir) / run_id
        path.mkdir(parents=True, exist_ok=False)
        return cls(path)

    @property
    def run_id(self) -> str:
        return self.path.name

    def write_run(self, run: RunRecord) -> None:
        _atomic_write(self.path / "run.json", (dump_line(run) + "\n").encode())

    def append_step(self, step: Step) -> None:
        data = step.model_dump(exclude_none=True)
        if step.type == "llm_call":  # the contract records unknown token counts/cost as explicit nulls
            for key in ("tokens_in", "tokens_out", "cost_usd"):
                data.setdefault(key, None)
        with open(self.path / "trace.jsonl", "a", encoding="utf-8") as f:
            f.write(dump_line(data) + "\n")
            f.flush()

    def touch_trace(self) -> None:
        (self.path / "trace.jsonl").touch()

    def write_output(self, output: Output) -> None:
        _atomic_write(self.path / "output.json", (dump_line(output) + "\n").encode())

    def read_run(self) -> RunRecord:
        return RunRecord.model_validate_json((self.path / "run.json").read_text())

    def read_steps(self, drop_truncated_last: bool = False) -> list[Step]:
        path = self.path / "trace.jsonl"
        if not path.exists():
            return []
        lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
        steps = []
        for i, line in enumerate(lines):
            try:
                steps.append(Step.model_validate_json(line))
            except ValueError:
                if drop_truncated_last and i == len(lines) - 1:
                    break
                raise
        return steps

    def read_output(self) -> Output:
        return Output.model_validate_json((self.path / "output.json").read_text())

    def atomic_write_many(self, files: dict[str, bytes]) -> None:
        """Stage every file in a fresh .eval-* directory, then rename each into place in the given order.

        Renames are individually atomic; callers put a file carrying hashes of the others last so an
        interruption between renames is detectable.
        """
        stage = Path(tempfile.mkdtemp(dir=self.path, prefix=".eval-"))
        try:
            for name, data in files.items():
                (stage / name).write_bytes(data)
            for name in files:
                os.replace(stage / name, self.path / name)
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def clean_stale_staging(self) -> None:
        for d in self.path.glob(".eval-*"):
            shutil.rmtree(d, ignore_errors=True)


# --- safe readers of untrusted run files (increment 16: moved here from ui/app.py, unchanged) ------------------


def is_regular(path: Path) -> bool:
    """A regular file, never a symlink (run directories are untrusted)."""
    return path.is_file() and not path.is_symlink()


def read_json(path: Path) -> dict | None:
    if not is_regular(path):
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_run(path: Path) -> dict | None:
    """run.json as a dict with the fields the UI shows, or None if it's missing, a symlink, or malformed."""
    f = path / "run.json"
    if not is_regular(f):
        return None
    try:
        run = json.loads(f.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(run, dict) or not isinstance(run.get("model"), dict):
        return None
    if not all(isinstance(run.get(k), str) for k in ("prompt_id", "status")):
        return None
    return run


def num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def float_or_none(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def read_scores(path: Path) -> dict | None:
    """scores.json reduced to the numbers the UI shows; None if missing, a symlink, or malformed."""
    f = path / "scores.json"
    if not is_regular(f):
        return None
    try:
        raw = json.loads(f.read_text())
        return {**raw, "overall": num(raw.get("overall")),
                "workflow": {**raw["workflow"], "score": num(raw["workflow"].get("score"))},
                "output": {**raw["output"], "score": num(raw["output"].get("score"))}}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def run_dirs(runs_dir: Path) -> list[Path]:
    """Every run directory: real directories (no symlinks, no dot-dirs) with a valid run id as their name."""
    if not runs_dir.is_dir():
        return []
    return [d for d in runs_dir.iterdir()
            if not d.is_symlink() and d.is_dir() and not d.name.startswith(".") and is_valid_run_id(d.name)]
