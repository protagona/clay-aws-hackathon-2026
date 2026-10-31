"""Writes to a task's prompts.jsonl and expected.jsonl (increment 13): one pid lock, whole-file temp + os.replace,
and the result re-read with the runner's own parsers before it replaces anything.

Lines other than the one being changed are copied through byte for byte (their keys, order, line endings and any blank
lines survive); a changed line keeps its other keys. A hand edit made between our read and our replace is detected by
the file's sha and refused, never overwritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path

from foundry.config import ConfigError
from foundry.data.tasks import PROMPT_ID_RE, TOOL_RE, RubricError, parse_expected, parse_prompts

MAX_TEXT = 20_000
MAX_STEPS = 12
MAX_TOOLS = 12
RESERVED = {"new", "tools", "."}  # /prompts/new, /api/prompts/tools, and "." which URLs normalise away
_SEPARATORS = {" ": "\\u2028", " ": "\\u2029", "\x85": "\\u0085"}  # str.splitlines() breaks lines on these


class StoreError(Exception):
    def __init__(self, status: int, message: str, field: str | None = None):
        super().__init__(message)
        self.status, self.message, self.field = status, message, field


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _dump(obj: dict) -> str:
    line = json.dumps(obj, ensure_ascii=False)
    for ch, esc in _SEPARATORS.items():
        line = line.replace(ch, esc)
    return line


class _File:
    """One JSONL file as read: its raw bytes (for the sha check and restore), lines split on "\\n" only."""

    def __init__(self, path: Path):
        self.path = path
        self.raw = path.read_bytes() if path.exists() else b""
        self.sha = hashlib.sha256(self.raw).hexdigest()
        self.lines = self.raw.decode("utf-8").split("\n") if self.raw else []
        self._reindex()

    def _reindex(self) -> None:
        self.index: dict[str, list[int]] = {}
        for i, line in enumerate(self.lines):
            try:
                obj = json.loads(line) if line.strip() else None
            except json.JSONDecodeError:
                continue  # the parsers report it with its line number when we validate
            if isinstance(obj, dict) and isinstance(obj.get("prompt_id"), str):
                self.index.setdefault(obj["prompt_id"], []).append(i)

    def ids(self) -> set[str]:
        return set(self.index)

    def obj(self, pid: str) -> dict | None:
        at = self.index.get(pid)
        return json.loads(self.lines[at[-1]]) if at else None

    def set(self, pid: str, obj: dict | None) -> None:
        """Replace the prompt's line (keeping its \\r if it had one), append a new one, or drop it (obj None)."""
        at = self.index.get(pid)
        if at:
            i = at[-1]
            if obj is None:
                del self.lines[i]
            else:
                self.lines[i] = _dump(obj) + ("\r" if self.lines[i].endswith("\r") else "")
        elif obj is not None:
            if self.lines and self.lines[-1] == "":
                self.lines.insert(len(self.lines) - 1, _dump(obj))
            else:
                self.lines += [_dump(obj), ""]
        self._reindex()

    def data(self) -> bytes:
        return "\n".join(self.lines).encode("utf-8")


class PromptStore:
    def __init__(self, task_dir: Path, task: str):
        self.dir, self.task = task_dir, task
        self.prompts_path, self.expected_path = task_dir / "prompts.jsonl", task_dir / "expected.jsonl"

    # --- reads --------------------------------------------------------------------------------------------------

    def rows(self) -> list[dict]:
        """Every prompt in file order: {prompt_id, prompt, archived, stages (or None)}."""
        prompts = self._parse(parse_prompts, self.prompts_path)
        expected = self._parse(parse_expected, self.expected_path)
        return [{"prompt_id": p.prompt_id, "prompt": p.prompt, "archived": p.archived,
                 "stages": expected[p.prompt_id].stages if p.prompt_id in expected else None} for p in prompts]

    def get(self, pid: str) -> dict | None:
        return next((r for r in self.rows() if r["prompt_id"] == pid), None)

    def next_id(self, taken: set[str]) -> str:
        used = {r["prompt_id"] for r in self.rows()} | set(self._parse(parse_expected, self.expected_path)) | taken
        n = 1
        while f"{self.task}-{n:03d}" in used:
            n += 1
        return f"{self.task}-{n:03d}"

    # --- writes -------------------------------------------------------------------------------------------------

    def create(self, pid: str, text: str, taken: set[str]) -> None:
        text = self._text(text)
        self._check_id(pid)
        with self._locked():
            prompts, expected = _File(self.prompts_path), _File(self.expected_path)
            self._no_duplicates(prompts)
            if pid in prompts.ids():
                raise StoreError(400, f"{pid} is already a prompt; pick another id", "prompt_id")
            if pid in expected.ids():
                raise StoreError(400, f"{pid} already has expected steps from an earlier prompt; pick another id", "prompt_id")
            if pid in taken:
                raise StoreError(400, f"{pid} already has runs from an earlier prompt; pick another id", "prompt_id")
            prompts.set(pid, {"prompt_id": pid, "prompt": text})
            self._write(prompts, parse_prompts)

    def set_text(self, pid: str, text: str) -> None:
        text = self._text(text)
        with self._locked():
            prompts = self._existing(pid)
            obj = prompts.obj(pid)
            obj["prompt"] = text
            prompts.set(pid, obj)
            self._write(prompts, parse_prompts)

    def set_archived(self, pid: str, archived: bool) -> None:
        with self._locked():
            prompts = self._existing(pid)
            obj = prompts.obj(pid)
            if archived:
                obj["archived"] = True
            else:
                obj.pop("archived", None)
            prompts.set(pid, obj)
            self._write(prompts, parse_prompts)

    def set_stages(self, pid: str, stages) -> str:
        """Save (or, with [], remove) the prompt's expected steps; returns the new expected.jsonl sha256."""
        clean = self._stages(stages)
        with self._locked():
            self._existing(pid)
            expected = _File(self.expected_path)
            self._no_duplicates(expected)  # a hand-made duplicate line would come back after its twin is removed
            expected.set(pid, {"prompt_id": pid, "stages": clean} if clean else None)
            self._write(expected, parse_expected)
            return hashlib.sha256(expected.data()).hexdigest()

    @staticmethod
    def _version_id(pid: str, used: set[str]) -> str:
        base = re.sub(r"-v\d+$", "", pid)
        n = 2
        while f"{base}-v{n}" in used:
            n += 1
        return f"{base}-v{n}"

    def next_version_id(self, pid: str, taken: set[str]) -> str | None:
        """The id copy_version would use now (None when it would be too long), for the button's label."""
        new = self._version_id(pid, _File(self.prompts_path).ids() | _File(self.expected_path).ids() | taken)
        return new if len(new) <= 64 else None

    def copy_version(self, pid: str, taken: set[str]) -> str:
        """A new prompt "<base>-vN" with the same text and steps; the original is untouched."""
        with self._locked():
            prompts = self._existing(pid)
            expected = _File(self.expected_path)
            self._no_duplicates(expected)
            new = self._version_id(pid, prompts.ids() | expected.ids() | taken)
            if len(new) > 64:
                raise StoreError(400, "The id is too long to version; create a new prompt instead", "prompt_id")
            src = prompts.obj(pid)
            steps = expected.obj(pid)
            before = expected.raw
            if steps is not None:  # steps first; if the prompt write then fails, put expected.jsonl back as it was
                expected.set(new, {"prompt_id": new, "stages": steps["stages"]})
                self._write(expected, parse_expected)
            prompts.set(new, {"prompt_id": new, "prompt": src["prompt"]})
            try:
                self._write(prompts, parse_prompts)
            except StoreError:
                if steps is not None:
                    tmp = self.expected_path.with_name(f"expected.jsonl.tmp-{os.getpid()}")
                    tmp.write_bytes(before)
                    os.replace(tmp, self.expected_path)
                raise
            return new

    # --- helpers ------------------------------------------------------------------------------------------------

    def _text(self, text) -> str:
        if not isinstance(text, str) or not text.strip():
            raise StoreError(400, "The prompt text is empty", "prompt")
        text = text.strip()
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            raise StoreError(400, "The prompt text has a character that can't be saved (a lone surrogate)", "prompt") from None
        if len(text) > MAX_TEXT:
            raise StoreError(400, f"The prompt text is {len(text):,} characters; the limit is {MAX_TEXT:,}", "prompt")
        return text

    @staticmethod
    def _check_id(pid) -> None:
        if not isinstance(pid, str) or not pid:
            raise StoreError(400, "Give the prompt an id", "prompt_id")
        if pid in RESERVED:
            raise StoreError(400, f"'{pid}' is reserved; pick another id", "prompt_id")
        if len(pid) > 64 or not PROMPT_ID_RE.match(pid) or ".." in pid:
            raise StoreError(400, "Use 1-64 letters, digits, '.', '_' or '-' (no '..')", "prompt_id")

    @staticmethod
    def _stages(stages) -> list[list[str]]:
        if not isinstance(stages, list) or not all(isinstance(s, list) for s in stages):
            raise StoreError(400, "Steps must be a list of steps, each a list of tool names", "stages")
        if len(stages) > MAX_STEPS:
            raise StoreError(400, f"At most {MAX_STEPS} steps", "stages")
        clean = []
        for n, step in enumerate(stages, start=1):
            if not step:
                raise StoreError(400, f"Step {n} has no tools; add one or remove the step", "stages")
            if len(step) > MAX_TOOLS:
                raise StoreError(400, f"Step {n} has more than {MAX_TOOLS} tools", "stages")
            bad = next((t for t in step if not isinstance(t, str) or not TOOL_RE.match(t)), None)
            if bad is not None:
                raise StoreError(400, f'Step {n}: "{bad}" isn\'t a tool name (use name or server/name)', "stages")
            clean.append(list(dict.fromkeys(step)))
        return clean

    def _existing(self, pid: str) -> _File:
        prompts = _File(self.prompts_path)
        self._no_duplicates(prompts)
        if pid not in prompts.ids():
            raise StoreError(404, f"No prompt {pid}")
        return prompts

    def _no_duplicates(self, f: _File) -> None:
        dup = {pid: at for pid, at in f.index.items() if len(at) > 1}
        if dup:
            pid, at = next(iter(dup.items()))
            lines = ", ".join(str(i + 1) for i in at)
            raise StoreError(400, f"tasks/{self.task}/{f.path.name} has {pid} on lines {lines}; fix the file by hand")

    def _write(self, f: _File, parse) -> None:
        tmp = f.path.with_name(f"{f.path.name}.tmp-{os.getpid()}")
        try:
            tmp.write_bytes(f.data())
            if f.path.exists():
                shutil.copymode(f.path, tmp)
            try:
                parse(tmp)
            except (ConfigError, RubricError) as exc:
                raise StoreError(400, self._name(str(exc), tmp, f.path)) from None
            now = f.path.read_bytes() if f.path.exists() else b""
            if hashlib.sha256(now).hexdigest() != f.sha:
                raise StoreError(409, f"{f.path.name} changed on disk while saving; reload and try again")
            os.replace(tmp, f.path)
            f.raw, f.sha = f.data(), hashlib.sha256(f.data()).hexdigest()
        finally:
            tmp.unlink(missing_ok=True)

    def _name(self, msg: str, tmp: Path, real: Path) -> str:
        """Loader messages name the temp file by absolute path; name the real file relative to the project."""
        return msg.replace(str(tmp), f"tasks/{self.task}/{real.name}").replace(str(real), f"tasks/{self.task}/{real.name}")

    def _parse(self, parse, path: Path):
        try:
            return parse(path)
        except (ConfigError, RubricError) as exc:
            raise StoreError(400, self._name(str(exc), path, path)) from None

    def _locked(self):
        return _Lock(self.dir / ".prompts.lock")


class _Lock:
    """O_EXCL lock file holding our pid, as RubricStore._lock: a lock whose pid is gone (a crash) is cleared."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    pid = int(self.path.read_text().strip() or 0)
                except (OSError, ValueError):
                    pid = 0
                if pid and _alive(pid):
                    raise StoreError(409, "Prompts are being changed; try again") from None
                self.path.unlink(missing_ok=True)
                continue
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return self
        raise StoreError(409, "Prompts are being changed; try again")

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)
