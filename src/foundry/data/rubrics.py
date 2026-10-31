"""Rubric drafts, activation and history under tasks/<task>/rubrics/ (increment 8, R1-R5, T2).

    rubric.json                         the active rubric (what evaluate reads; unchanged contract)
    rubrics/drafts/<id>/                source.<ext>, draft.json, meta.json   (until approved or discarded)
    rubrics/sources/<id>/               the same, kept after activation for provenance
    rubrics/history/<id>@<version>.json every version ever activated, byte-identical
    rubrics/history.jsonl               one line per activation (incl. rollbacks)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
import json
import os
import re
import secrets
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from foundry.contract import Rubric

# --- rubric records and validation (increment 16, ADR-3: moved down from rubrics/draft.py and sources.py, so the
# store never imports the engine; drafting with a model stays in rubrics/draft.py) ---------------------------------

@dataclass(frozen=True)
class Source:
    kind: str  # file | text | url | mcp
    label: str  # file name, "pasted text", the URL, or server/tool
    fmt: str  # a FORMATS value
    data: bytes


@dataclass
class DraftResult:
    rubric: dict | None  # the validated rubric (as JSON-ready dict), or None when the reply never validated
    raw: str = ""  # the last model reply (or the source itself for the JSON shortcut)
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    model: str | None = None
    prompt_id: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    partial: dict | None = None  # whatever JSON parsed from an invalid reply, to prefill the review form


def validation_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    where = ".".join(str(p) for p in err["loc"]) or "(rubric)"
    return f"{where}: {err['msg'].removeprefix('Value error, ')}"


MAX_WEIGHT = 100  # finite and bounded: Infinity or 1e308 would break the weighted score
MAX_LEVELS = 20  # the review form shows one field per level; wider scales are banded by the model (prompt v3)
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")


def canonical(obj: dict, task_id: str) -> dict:
    """Only the fields the review form shows (so nothing unreviewed reaches the judge), levels 1..max only, and the
    target task. Raises ValidationError/ValueError when the result is not a valid rubric."""
    crits = obj.get("criteria") if isinstance(obj.get("criteria"), list) else []
    out = {"rubric_id": obj.get("rubric_id"), "task_id": task_id, "version": obj.get("version"),
           "description": obj.get("description") or "", "criteria": []}
    for c in crits:
        if not isinstance(c, dict):
            continue
        levels = c.get("levels") if isinstance(c.get("levels"), dict) else {}
        mx = c.get("max")
        keep = {str(i) for i in range(1, mx + 1)} if isinstance(mx, int) and not isinstance(mx, bool) and mx < 100 else set()
        weight = c.get("weight", 1)
        if isinstance(weight, bool):
            raise ValueError(f"criteria.{len(out['criteria'])}.weight: must be a number")
        try:
            w = float(weight)
        except (TypeError, ValueError, OverflowError):  # a 400-digit integer overflows float()
            w = float("inf") if isinstance(weight, int) else None
        if w is not None and not (0 < w <= MAX_WEIGHT):  # also "inf", "1e308"
            raise ValueError(f"criteria.{len(out['criteria'])}.weight: must be above 0 and at most {MAX_WEIGHT}")
        if isinstance(mx, int) and not isinstance(mx, bool) and mx > MAX_LEVELS:
            raise ValueError(f"criteria.{len(out['criteria'])}.max: use a scale of 2 to {MAX_LEVELS} levels "
                             "(map wider scales to bands)")
        out["criteria"].append({"id": c.get("id"), "name": c.get("name"), "description": c.get("description"),
                                "max": mx, "weight": weight,
                                "levels": {k: v for k, v in levels.items() if k in keep}})
    rubric = Rubric.model_validate(out)
    if not NAME_RE.match(rubric.rubric_id):
        raise ValueError("rubric_id: use letters, digits, '.', '_' or '-' only (max 100)")
    if not VERSION_RE.match(rubric.version):
        raise ValueError("version: use letters, digits, '.', '_' or '-' only (max 40)")
    return rubric.model_dump(include={"rubric_id", "task_id", "version", "description", "criteria"})


def form_shape(obj) -> dict:
    """Any draft body (even a malformed model reply) reshaped so the review form can always render it."""
    obj = obj if isinstance(obj, dict) else {}
    crits = obj.get("criteria") if isinstance(obj.get("criteria"), list) else []

    def s(v):
        return v if isinstance(v, str) else "" if v is None else str(v)

    out = []
    for c in crits[:50]:
        c = c if isinstance(c, dict) else {}
        mx = c.get("max")
        levels = c.get("levels")
        if isinstance(levels, list):  # ["poor", "ok", "good"] -> {"1": "poor", ...}
            levels = {str(i + 1): v for i, v in enumerate(levels)}
        out.append({"id": s(c.get("id")), "name": s(c.get("name")), "description": s(c.get("description")),
                    "max": mx if isinstance(mx, int) and not isinstance(mx, bool) and 2 <= mx <= MAX_LEVELS else 4,
                    "weight": c.get("weight") if isinstance(c.get("weight"), (int, float)) else 1,
                    "levels": {s(k): s(v) for k, v in levels.items()} if isinstance(levels, dict) else {}})
    return {"rubric_id": s(obj.get("rubric_id")), "version": s(obj.get("version")),
            "description": s(obj.get("description")), "criteria": out}


def check(obj: dict, task_id: str) -> tuple[dict | None, str | None]:
    """(canonical rubric, None) or (None, error message)."""
    try:
        return canonical(obj, task_id), None
    except ValidationError as exc:
        return None, validation_error(exc)
    except (ValueError, OverflowError, RecursionError) as exc:
        return None, str(exc)[:300] or type(exc).__name__



DRAFT_ID_RE = re.compile(r"^[a-f0-9]{12}$")
HISTORY_FILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}@[A-Za-z0-9._-]{1,40}\.json$")
SAFE = re.compile(r"[^A-Za-z0-9._-]")
_LOCK = threading.Lock()


class StoreError(Exception):
    pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dump(rubric: dict) -> bytes:
    return (json.dumps(rubric, indent=2, ensure_ascii=False) + "\n").encode()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def history_name(rubric_id: str, version: str) -> str:
    return f"{SAFE.sub('-', rubric_id)[:100]}@{SAFE.sub('-', version)[:40]}.json"


class Store:
    def __init__(self, task_dir: Path):
        self.task = Path(task_dir)
        self.base = self.task / "rubrics"

    # --- drafts -----------------------------------------------------------------------------------------------

    def _draft_dir(self, draft_id: str, where: str = "drafts") -> Path:
        if not DRAFT_ID_RE.match(draft_id or ""):
            raise StoreError("Unknown draft")
        d = self.base / where / draft_id
        if not d.is_dir() or d.is_symlink():
            raise StoreError("Unknown draft")
        return d

    def save_draft(self, source: Source, result: DraftResult) -> str:
        draft_id = secrets.token_hex(6)
        d = self.base / "drafts" / draft_id
        d.mkdir(parents=True)
        (d / f"source.{source.fmt}").write_bytes(source.data)
        body = result.rubric or form_shape(result.partial)  # an invalid reply is saved form-shaped (never crashes review)
        _write(d / "draft.json", _dump(body))
        meta = {"draft_id": draft_id, "created_at": _now(), "valid": result.rubric is not None, "error": result.error,
                "source": {"kind": source.kind, "label": source.label, "format": source.fmt,
                           "bytes": len(source.data), "sha256": _sha(source.data)},
                "model": result.model, "prompt_id": result.prompt_id, "notes": result.notes,
                "usage": {"tokens_in": result.tokens_in, "tokens_out": result.tokens_out},
                "raw_reply": result.raw[:20000] if result.rubric is None else None}
        _write(d / "meta.json", (json.dumps(meta, indent=2) + "\n").encode())
        return draft_id

    def drafts(self) -> list[dict]:
        root = self.base / "drafts"
        out = []
        for d in sorted(root.iterdir()) if root.is_dir() else []:
            if DRAFT_ID_RE.match(d.name) and d.is_dir() and not d.is_symlink():
                try:
                    out.append(json.loads((d / "meta.json").read_text()))
                except (OSError, ValueError):
                    continue
        return sorted(out, key=lambda m: m.get("created_at", ""), reverse=True)

    def write_draft_meta(self, draft_id: str, meta: dict) -> None:
        """Replace a draft's meta.json (the rubric service adds the drafting cost once it knows the price)."""
        if not DRAFT_ID_RE.match(draft_id):
            raise StoreError(f"Unknown draft {draft_id}")
        (self.base / "drafts" / draft_id / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    def load_draft(self, draft_id: str) -> tuple[dict, dict, Path]:
        d = self._draft_dir(draft_id)
        try:
            meta = json.loads((d / "meta.json").read_text())
            body = json.loads((d / "draft.json").read_text())
        except (OSError, ValueError):
            raise StoreError("This draft is unreadable; discard it") from None
        src = next((p for p in d.iterdir() if p.name.startswith("source.")), None)
        shaped = body if isinstance(body, dict) and check(dict(body), self.task.name)[0] is not None else form_shape(body)
        return meta, shaped, src

    def update_draft(self, draft_id: str, body: dict) -> str | None:
        """Save an edited draft; returns the validation error (or None when it is a valid rubric)."""
        d = self._draft_dir(draft_id)
        rubric, error = check(dict(body), self.task.name)
        _write(d / "draft.json", _dump(rubric or form_shape(body)))
        meta = json.loads((d / "meta.json").read_text())
        meta["valid"], meta["error"] = rubric is not None, error
        _write(d / "meta.json", (json.dumps(meta, indent=2) + "\n").encode())
        return meta["error"]

    def discard(self, draft_id: str) -> None:
        shutil.rmtree(self._draft_dir(draft_id))

    # --- activation & history ---------------------------------------------------------------------------------

    def history(self) -> list[dict]:
        f = self.base / "history.jsonl"
        out = []
        for line in f.read_text().splitlines() if f.exists() else []:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict) and HISTORY_FILE_RE.match(str(e.get("file", ""))) and isinstance(e.get("sha256"), str) \
                    and all(isinstance(e.get(k), str) for k in ("rubric_id", "version")) \
                    and all(e.get(k) is None or isinstance(e[k], str) for k in ("activated_at", "note", "draft_id")) \
                    and (e.get("source") is None or (isinstance(e["source"], dict)
                                                     and all(v is None or isinstance(v, str) for v in e["source"].values()))):
                out.append({**e, "activated_at": e.get("activated_at")})  # the API's types (increment 17)
        return out

    def active_sha(self) -> str | None:
        f = self.task / "rubric.json"
        return _sha(f.read_bytes()) if f.exists() else None

    def history_file(self, name: str) -> Path:
        if not HISTORY_FILE_RE.match(name or ""):
            raise StoreError("Unknown rubric version")
        p = self.base / "history" / name
        if not p.is_file() or p.is_symlink():
            raise StoreError("Unknown rubric version")
        return p

    def _append(self, entry: dict) -> None:
        f = self.base / "history.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    def _archive_current(self) -> None:
        """Before the first activation, the hand-written rubric.json goes into history as it is."""
        cur = self.task / "rubric.json"
        if not cur.exists():
            return
        data = cur.read_bytes()
        sha = _sha(data)
        if any(e.get("sha256") == sha for e in self.history()):
            return
        try:
            r = Rubric.model_validate_json(data)
        except ValidationError:  # broken by hand: keep its bytes anyway (it can't be re-activated until fixed)
            name = f"invalid@{sha[:8]}.json"
            if not (self.base / "history" / name).exists():
                _write(self.base / "history" / name, data)
            self._append({"rubric_id": "invalid", "version": sha[:8], "sha256": sha, "activated_at": None,
                          "draft_id": None, "source": None, "file": name, "note": "archived invalid rubric.json"})
            return
        name = history_name(r.rubric_id, r.version)
        target = self.base / "history" / name
        if target.exists() and target.read_bytes() != data:  # hand-edited without a version bump: keep both
            name = history_name(r.rubric_id, f"{r.version}-{sha[:8]}")
            target = self.base / "history" / name
        if not target.exists():
            _write(target, data)
        self._append({"rubric_id": r.rubric_id, "version": r.version, "sha256": sha, "activated_at": None,
                      "draft_id": None, "source": None, "file": name, "note": "archived original"})

    def _lock(self):
        """O_EXCL lock file holding our pid; a lock whose pid is gone (a crash) is cleared and retaken."""
        lock = self.base / ".rubric.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    pid = int(lock.read_text().strip() or 0)
                except (OSError, ValueError):
                    pid = 0
                if pid and _alive(pid):
                    raise StoreError("Another rubric activation is in progress; try again in a moment") from None
                lock.unlink(missing_ok=True)
                continue
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return lock
        raise StoreError("Could not take the rubric lock; try again")

    def activate_draft(self, draft_id: str) -> dict:
        with _LOCK:
            lock = self._lock()
            try:
                meta, body, _ = self.load_draft(draft_id)
                canon, error = check(dict(body), self.task.name)
                if canon is None:
                    raise StoreError(f"Fix the draft first: {error}")
                rubric = Rubric.model_validate(canon)
                self._archive_current()
                name = history_name(rubric.rubric_id, rubric.version)
                data = _dump(canon)
                target = self.base / "history" / name
                if target.exists() and target.read_bytes() != data:
                    raise StoreError(f"{rubric.rubric_id} v{rubric.version} already exists in the history: "
                                     "bump the version")
                _write(target, data)
                _write(self.task / "rubric.json", data)
                entry = {"rubric_id": rubric.rubric_id, "version": rubric.version, "sha256": _sha(data),
                         "activated_at": _now(), "draft_id": draft_id, "file": name,
                         "source": {k: meta["source"].get(k) for k in ("kind", "label", "sha256")}}
                self._append(entry)
                dest = self.base / "sources" / draft_id
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(self._draft_dir(draft_id)), dest)
                return entry
            finally:
                lock.unlink(missing_ok=True)

    def activate_version(self, name: str) -> dict:
        """R5: roll back (or forward) to a version already in the history."""
        with _LOCK:
            lock = self._lock()
            try:
                self._archive_current()
                data = self.history_file(name).read_bytes()
                r = Rubric.model_validate_json(data)
                _write(self.task / "rubric.json", data)
                entry = {"rubric_id": r.rubric_id, "version": r.version, "sha256": _sha(data), "activated_at": _now(),
                         "draft_id": None, "source": None, "file": name, "note": "re-activated from history"}
                self._append(entry)
                return entry
            finally:
                lock.unlink(missing_ok=True)

    def versions(self, runs_dir: Path | None = None) -> list[dict]:
        """One row per distinct version (latest activation first), with how many runs it scored (T2)."""
        counts: dict[str, int] = {}
        if runs_dir is not None and Path(runs_dir).is_dir():
            for f in Path(runs_dir).glob("*/scores.json"):
                try:
                    sha = json.loads(f.read_text()).get("rubric_sha256")
                except (OSError, ValueError, AttributeError):
                    continue
                if isinstance(sha, str):
                    counts[sha] = counts.get(sha, 0) + 1
        active = self.active_sha()
        rows: dict[str, dict] = {}
        for e in self.history():
            rows[e["sha256"]] = {**e, "runs": counts.get(e["sha256"], 0), "active": e["sha256"] == active}
        return sorted(rows.values(), key=lambda r: r.get("activated_at") or "", reverse=True)


def read_source(path: Path, limit: int | None = None) -> bytes:
    """A draft's saved source file (its first `limit` bytes, or all of it)."""
    data = path.read_bytes()
    return data if limit is None else data[:limit]
