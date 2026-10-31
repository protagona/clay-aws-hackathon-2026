"""What the UI can read and do, independent of how it is rendered (increment 9, A2).

The classic Jinja pages and the JSON API both call these; errors are raised as UiError(status, message) and each
front end turns them into its own error response. Untrusted run files are read through the same checks as before.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from pathlib import Path

from foundry.config import ConfigError, load_config
from foundry.contract import evaluation_interrupted
from foundry.evaluate import lock
from foundry.runstore import RunDir, is_valid_run_id, new_run_id
from foundry.prompts.store import PromptStore, StoreError
from foundry.tasks import RubricError, parse_prompts
from foundry.ui import dashboard
from foundry.ui.monitor import monitor
from foundry.ui.render import render


class UiError(Exception):
    def __init__(self, status: int, message: str, field: str | None = None):
        super().__init__(message)
        self.status, self.message, self.field = status, message, field  # field: the form field it belongs to


def _core():
    from foundry.ui import app as core  # the module-level helpers live there (loaded before any request)

    return core


# --- runs ------------------------------------------------------------------------------------------------------


def run_path(st, run_id: str) -> Path | None:
    core = _core()
    if not is_valid_run_id(run_id) or run_id.startswith("."):
        return None
    raw = st.runs_dir / run_id
    if raw.is_symlink():
        return None
    path = raw.resolve()
    if not path.is_relative_to(st.runs_dir) or core._read_run(path) is None:
        return None
    return path


def need_run(st, run_id: str) -> Path:
    path = run_path(st, run_id)
    if path is None:
        raise UiError(404, "Run not found")
    return path


def run_view(st, path: Path, seen: int | None = None) -> dict:
    """Everything the run page / drawer shows, from the run's own files."""
    core = _core()
    rd = RunDir(path)
    run = {**core._read_run(path), "run_id": path.name}  # links use the directory name, never run.json's
    scores = core._scores(path)
    interrupted = scores is not None and evaluation_interrupted(path)
    report_html = None
    if scores is not None and not interrupted and core._regular(path / "report.md"):
        report_html = render((path / "report.md").read_text())
    steps = []
    try:
        if core._regular(path / "trace.jsonl"):
            steps = rd.read_steps(drop_truncated_last=True)
    except ValueError:
        pass
    evaluating = lock.is_locked(path)
    live = evaluating or run.get("status") == "running"
    return {"run": run, "scores": None if interrupted else scores, "report_html": report_html,
            "rubric_info": None if interrupted else core._rubric_info(st, run, scores),
            "interrupted": interrupted, "evaluating": evaluating, "m": monitor(run, steps, live=live, seen=seen)}


def board_column(card: dict) -> str:
    """Which live-board column a run card belongs in (increment 9, U2; computed here so the UI stays thin)."""
    h = card.get("headline")
    if h == "running":
        return "running"
    if h == "evaluating":
        return "evaluating"
    if card.get("status") == "succeeded" and h not in ("not evaluated", "incomplete"):
        return "scored"
    return "attention"


def start_run(st, prompt_id: str, config: str) -> str:
    core = _core()
    configs = core._configs(st)
    if config not in configs:
        raise UiError(400, f"Unknown config '{config}'. Available: {', '.join(configs)}")
    try:
        cfg = load_config(st.root / "config" / f"{config}.toml")
        prompts = task_prompts(st, cfg.task.id)
    except ConfigError as exc:
        raise UiError(400, _rel(st, str(exc))) from None
    _check_runnable(st, cfg, config, [prompt_id], prompts)
    with st.lock:
        active = next((j for j in st.jobs.values() if j.kind == "run" and j.alive), None)
        if active is not None:
            raise UiError(409, f"A run is already in progress: {active.run_id}")
        run_id = new_run_id(prompt_id)
        core._spawn(st, "run", run_id, f"Run {prompt_id}",
                    ["run", "--config", f"config/{config}.toml", "--prompt", prompt_id,
                     "--runs-dir", str(st.runs_dir), "--run-id", run_id])
    return run_id


def start_evaluate(st, run_id: str) -> None:
    core = _core()
    path = need_run(st, run_id)
    run = core._read_run(path)
    if run.get("status") == "running":
        raise UiError(409, f"Run {run_id} is still running. If it crashed, score it from the CLI: foundry evaluate runs/{run_id} --force.")
    try:
        token = lock.acquire(path, owner="ui")
    except lock.LockHeld as exc:
        raise UiError(409, str(exc)) from None
    config = core._eval_config_for(st, run)
    with st.lock:
        core._spawn(st, "evaluate", run_id, f"Evaluation of {run_id}",
                    ["evaluate", str(path), "--config", config, "--lock-held", token], lock_token=token, lock_dir=path)


def compare(st, a: str, b: str) -> tuple[dict, dict, list[dict]]:
    core = _core()
    pa, pb = run_path(st, a), run_path(st, b)
    if pa is None or pb is None:
        raise UiError(404, "Run not found")
    sa, sb = core._compare_side(pa), core._compare_side(pb)
    return sa, sb, core._compare_rows(sa, sb)


# --- dashboard -------------------------------------------------------------------------------------------------


def board_kpis(st, entries: list[dict] | None = None) -> dict:
    """Runs-board KPIs (increment 9, A5): spend over every run and evaluation; mean over the active rubric only."""
    core = _core()
    entries = entries if entries is not None else _entries(st)
    _, active = core._rubric_options(st, entries)
    every = dashboard.build(entries)["kpis"]
    scored = dashboard.build([e for e in entries if (e["scores"] or {}).get("rubric_sha256") in active])["kpis"]
    # Each run's share (increment 11), so the board can total just the runs its filters show.
    shares = {}
    for e in entries:
        overall = (e["scores"] or {}).get("overall") if (e["scores"] or {}).get("rubric_sha256") in active else None
        shares[e["run_id"]] = {"spent": sum(dashboard.costs(e)),
                               "overall": float(overall) if isinstance(overall, (int, float)) and not isinstance(overall, bool) else None}
    return {"spent": every["spent"], "mean": scored["mean"], "scored": scored["scored"], "shares": shares}


def _entries(st) -> list[dict]:
    core = _core()
    entries = []
    for d in core._run_dirs(st):
        run = core._read_run(d)
        if run is None:
            continue
        scores = core._scores(d)
        if scores is not None and evaluation_interrupted(d):
            scores = None
        steps = []
        try:
            if core._regular(d / "trace.jsonl"):
                steps = RunDir(d).read_steps(drop_truncated_last=True)
        except ValueError:
            pass
        entries.append({"run_id": d.name, "run": run, "scores": scores, "steps": steps})
    return entries


def dashboard_view(st, rubric: str = "active") -> dict:
    core = _core()
    entries = _entries(st)
    # Scores from different rubrics are not comparable, so the default is the active rubric(s) only.
    options, active = core._rubric_options(st, entries)
    chosen = active if rubric == "active" else (None if rubric == "all" else
                                                 ({rubric} if any(o["sha"] == rubric for o in options) else active))
    shown = entries if chosen is None else [e for e in entries if (e["scores"] or {}).get("rubric_sha256") in chosen]
    mixed = chosen is None and len({(e["scores"] or {}).get("rubric_sha256") for e in entries if e["scores"]}) > 1
    scored_by: dict[str, str] = {}  # rubric sha -> one run scored with it, to open that rubric's copy (increment 10)
    for e in entries:
        sha = (e["scores"] or {}).get("rubric_sha256")
        if isinstance(sha, str):
            scored_by.setdefault(sha, e["run_id"])
    return {"d": dashboard.build(shown), "rubric_options": options, "scored_by": scored_by,
            "rubric_choice": rubric if rubric in ("active", "all") or chosen is not active else "active",
            "mixed": mixed, "total_runs": len(entries)}


# --- batches ---------------------------------------------------------------------------------------------------


def start_batch(st, config: str, models: list[str], prompts: list[str], repeats: int, concurrency: int,
                evaluate: bool, max_cost: float) -> str:
    core = _core()
    if config not in core._configs(st):
        raise UiError(400, f"Unknown config '{config}'. Available: {', '.join(core._configs(st))}")
    unknown = [m for m in models if m not in core._model_choices(st)]
    if unknown:
        raise UiError(400, f"Unknown model(s): {', '.join(unknown)}")
    if not prompts:
        raise UiError(400, f"Pick one or more prompts from: {', '.join(core._prompt_ids(st))}")
    try:
        cfg = load_config(st.root / "config" / f"{config}.toml")
        _check_runnable(st, cfg, config, prompts, task_prompts(st, cfg.task.id))
    except ConfigError as exc:
        raise UiError(400, _rel(st, str(exc))) from None
    if not (1 <= repeats <= 5 and 1 <= concurrency <= 4 and 0 < max_cost <= 50):
        raise UiError(400, "Repeats must be 1-5, parallel runs 1-4, and the cost cap between $0 and $50.")
    with st.lock:
        active = next((j for j in st.jobs.values() if j.kind == "batch" and j.alive), None)
        if active is not None:
            raise UiError(409, f"A batch is already running: {active.run_id}")
        batch_id = time.strftime("b-%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        args = ["batch", "--config", f"config/{config}.toml", "--prompts", ",".join(prompts),
                "--repeats", str(repeats), "--concurrency", str(concurrency), "--max-cost", f"{max_cost:.2f}",
                "--out", str(st.batches_dir / batch_id), "--runs-dir", str(st.runs_dir), "--run-prefix", f"{batch_id}_"]
        if models:
            args += ["--models", ",".join(models)]
        if evaluate:
            args.append("--evaluate")
        job = core._spawn(st, "batch", batch_id, f"Batch {batch_id}", args)
        job.prompts = list(prompts)  # a queued job has no run dir yet; this keeps those prompts' text locked
    return batch_id


def batch_view(st, batch_id: str) -> dict:
    core = _core()
    path = st.batches_dir / batch_id
    if not core.BATCH_ID_RE.match(batch_id) or not path.is_dir():
        raise UiError(404, "Batch not found")
    job = next((j for j in st.jobs.values() if j.kind == "batch" and j.run_id == batch_id), None)
    meta = core._read_json(path / "batch.json") or {}
    rows = core._batch_rows(st, path)
    running = (job is not None and job.alive) or (job is None and meta.get("status") == "running"
                                                   and not meta.get("exit_code"))
    if job is not None and not job.alive and meta.get("status") != "finished":
        meta["status"] = "failed to start" if job.exit_code == 2 else "stopped"
    return {"batch_id": batch_id, "meta": meta, "rows": rows, "variants": core._variant_stats(rows), "job": job,
            "running": running, "done": sum(r["status"] not in ("pending", "") for r in rows)}


# --- import & receiver -----------------------------------------------------------------------------------------


def check_import_form(st, task: str, prompt_id: str, config: str) -> None:
    core = _core()
    if task not in core._task_ids(st) or not is_valid_run_id(prompt_id) or (config and config not in core._configs(st)):
        raise UiError(400, "Pick a task, a prompt id of letters/digits/._- and a known config.")


def import_saved_trace(st, dest: Path, filename: str, task: str, prompt_id: str, config: str) -> list[Path]:
    from foundry.importer import ImportError_, import_spans, spans_from_file

    try:
        prices = load_config(st.root / "config" / f"{config}.toml").prices if config else None
        return import_spans(spans_from_file(dest), st.runs_dir, task, prompt_id,
                            f"upload:{Path(filename or 'trace').name}", prices)
    except (ImportError_, ConfigError) as exc:
        detail = str(exc).replace(str(dest), Path(filename or "trace").name)  # never show the server's upload path
        raise UiError(400, f"Could not import {filename}: {detail}") from None


def receiver_start(st, task: str, prompt_id: str, config: str) -> None:
    from foundry.receiver import Receiver

    check_import_form(st, task, prompt_id, config)
    with st.lock:
        if st.receiver is None:
            try:
                prices = load_config(st.root / "config" / f"{config}.toml").prices if config else None
                st.receiver = Receiver(st.runs_dir, task, prompt_id, prices, port=st.receiver_port,
                                       log=lambda *_: None).start()
            except OSError as exc:
                raise UiError(409, f"Cannot listen on 127.0.0.1:{st.receiver_port}: {exc.strerror}. "
                                   "Is another receiver running?") from None


def receiver_stop(st) -> None:
    with st.lock:
        if st.receiver is not None:
            st.receiver.close()
            st.receiver = None


# --- rubric ----------------------------------------------------------------------------------------------------


def rubric_store(st, task: str):
    from foundry.rubrics.store import Store

    if task not in _core()._task_ids(st):
        raise UiError(404, "Unknown task")
    return Store(st.root / "tasks" / task)


def rubric_overview(st, task: str) -> dict:
    from foundry.tasks import RubricError, load_rubric

    store = rubric_store(st, task)
    try:
        active, _ = load_rubric(store.task / "rubric.json")
    except RubricError:
        active = None
    versions = store.versions(st.runs_dir)
    return {"task": task, "active": active, "versions": versions, "drafts": store.drafts(),
            "active_entry": next((v for v in versions if v["active"]), None)}


def rubric_create_draft(st, task: str, kind: str, config: str, *, file_name: str = "", file_data: bytes = b"",
                        text: str = "", url: str = "", mcp: str = "", args: str = "") -> str:
    """Blocking (up to a minute): acquire the source and draft it. Call from a worker thread."""
    from foundry.models.base import ProviderError
    from foundry.rubrics.service import acquire, create_draft
    from foundry.rubrics.sources import SourceError

    store = rubric_store(st, task)
    if kind == "file" and not file_name:  # filled another field without switching the source type
        kind = "text" if text.strip() else "url" if url.strip() else "mcp" if mcp.strip() else kind
    if config not in _core()._configs(st) or kind not in ("file", "text", "url", "mcp"):
        raise UiError(400, "Pick a task, a known config and a source.")
    if kind == "file" and not file_name:
        raise UiError(400, "Choose a file to upload.")
    try:
        cfg = load_config(st.root / "config" / f"{config}.toml")
        source = acquire(kind, cfg, file_name=file_name, file_data=file_data, text=text, url=url, mcp=mcp, args=args)
        draft_id, _ = create_draft(store.task, source, cfg)
    except (SourceError, ConfigError) as exc:
        raise UiError(400, str(exc)) from None
    except ProviderError as exc:
        raise UiError(502, f"The model could not draft the rubric: {exc}") from None
    return draft_id


def rubric_draft(st, task: str, draft_id: str) -> dict:
    from foundry.rubrics.sources import BINARY
    from foundry.rubrics.store import StoreError

    store = rubric_store(st, task)
    try:
        meta, body, src = store.load_draft(draft_id)
    except StoreError as exc:
        raise UiError(404, str(exc)) from None
    text = None
    if src is not None and meta["source"]["format"] not in BINARY:
        text = src.read_bytes()[:50_000].decode("utf-8", "replace")
    return {"task": task, "draft_id": draft_id, "meta": meta, "body": body, "source_text": text}


def rubric_update_draft(st, task: str, draft_id: str, body: dict, activate: bool = False) -> str | None:
    """Save an edited draft; returns its validation error (None when valid). With activate, also activates it."""
    from foundry.rubrics.store import StoreError

    store = rubric_store(st, task)
    try:
        store.load_draft(draft_id)
        err = store.update_draft(draft_id, body)
        if activate:
            if err:
                raise UiError(400, f"Not activated: {err}")
            store.activate_draft(draft_id)
    except StoreError as exc:
        raise UiError(404 if "Unknown draft" in str(exc) else 409, str(exc)) from None
    return err


def rubric_discard(st, task: str, draft_id: str) -> None:
    from foundry.rubrics.store import StoreError

    try:
        rubric_store(st, task).discard(draft_id)
    except StoreError as exc:
        raise UiError(404, str(exc)) from None


def rubric_source(st, task: str, draft_id: str) -> Path:
    from foundry.rubrics.store import StoreError

    try:
        _, _, src = rubric_store(st, task).load_draft(draft_id)
    except (StoreError, UiError):
        src = None
    if src is None:
        raise UiError(404, "Not found")
    return src


def rubric_version(st, task: str, name: str) -> dict:
    from foundry.rubrics.store import StoreError
    from foundry.tasks import RubricError, load_rubric

    store = rubric_store(st, task)
    try:
        rubric, raw = load_rubric(store.history_file(name))
    except (StoreError, RubricError):
        raise UiError(404, "Unknown rubric version") from None
    return {"task": task, "rubric": rubric, "active": hashlib.sha256(raw).hexdigest() == store.active_sha()}


def _rubric_with_meta(store, path: Path) -> dict:
    from foundry.tasks import RubricError, load_rubric

    try:
        rubric, raw = load_rubric(path)
    except RubricError:
        raise UiError(404, "No valid rubric there") from None
    sha = hashlib.sha256(raw).hexdigest()
    file = next((e["file"] for e in store.history() if e.get("sha256") == sha), None)
    return {"rubric": rubric, "sha256": sha, "active": sha == store.active_sha(), "file": file}


def rubric_active(st, task: str) -> dict:
    """Increment 10, R1: the active rubric in full, with the history file holding the same bytes (if any)."""
    store = rubric_store(st, task)
    return _rubric_with_meta(store, store.task / "rubric.json")


def run_rubric(st, run_id: str) -> dict:
    """Increment 10, R2: the exact rubric copied into a run when it was evaluated (untrusted: regular file only)."""
    core = _core()
    path = need_run(st, run_id)
    copy = path / "rubric.json"
    if not core._regular(copy):
        raise UiError(404, "This run has no rubric copy (not scored yet)")
    task = (core._read_run(path) or {}).get("task_id")
    store = rubric_store(st, task if isinstance(task, str) and task in core._task_ids(st) else "adb")
    return _rubric_with_meta(store, copy)


def rubric_activate_version(st, task: str, name: str) -> None:
    from foundry.rubrics.store import StoreError

    try:
        rubric_store(st, task).activate_version(name)
    except StoreError as exc:
        raise UiError(409 if "progress" in str(exc) else 404, str(exc)) from None


# --- prompts (increment 13) -----------------------------------------------------------------------------------


def _rel(st, msg: str) -> str:
    """Error text without the server's absolute paths."""
    return msg.replace(f"{st.root}/", "").replace(str(st.root), ".")


def task_prompts(st, task: str) -> list:
    """Every prompt of a task in this UI's own root (archived ones included)."""
    path = st.root / "tasks" / task / "prompts.jsonl"
    if not path.is_file():
        raise ConfigError(f"tasks/{task}/prompts.jsonl", "(file)", f"no prompts for task '{task}'")
    try:
        return parse_prompts(path)
    except ConfigError as exc:
        raise UiError(400, _rel(st, str(exc))) from None


def _check_runnable(st, cfg, config: str, ids: list[str], prompts: list) -> None:
    """Known and not archived; a config that replays scripts must have one for each prompt (P7, P10)."""
    by_id = {p.prompt_id: p for p in prompts}
    for pid in ids:
        p = by_id.get(pid)
        if p is None:
            active = ", ".join(q.prompt_id for q in prompts if not q.archived)
            raise UiError(400, f"Unknown prompt id '{pid}' for task '{cfg.task.id}'. Available: {active}")
        if p.archived:
            raise UiError(400, f"{pid} is archived. Unarchive it on the Prompts page to run it.")
        if cfg.model.provider == "scripted" and cfg.model.script:
            script = Path(cfg.model.script.format(prompt_id=pid))
            if not (script if script.is_absolute() else st.root / script).is_file():
                raise UiError(400, f"The offline config '{config}' has no script for {pid}. Pick a Bedrock config.")


def prompt_store(st, task: str) -> PromptStore:
    if task not in _core()._task_ids(st):
        raise UiError(400, f"Unknown task '{task}'")
    return PromptStore(st.root / "tasks" / task, task)


def _store(st, fn, *args):
    try:
        return fn(*args)
    except StoreError as exc:
        raise UiError(exc.status, _rel(st, exc.message), exc.field) from None
    except (ConfigError, RubricError) as exc:
        raise UiError(400, _rel(st, str(exc))) from None


def _runs_by_prompt(st, task: str) -> tuple[dict[str, list[dict]], list[str]]:
    """Readable runs of the task grouped by prompt id, and the names of unreadable run dirs (counted by name)."""
    core = _core()
    by: dict[str, list[dict]] = {}
    unreadable = []
    for d in core._run_dirs(st):
        run = core._read_run(d)
        if run is None:
            unreadable.append(d.name)
        elif run.get("task_id") == task and isinstance(run.get("prompt_id"), str):
            by.setdefault(run["prompt_id"], []).append({"run_id": d.name, "run": run})
    return by, unreadable


def _pending(st, pid: str) -> bool:
    """A live UI run of this prompt, or a live batch that includes it (read without st.lock: callers may hold it)."""
    for j in list(st.jobs.values()):
        if not j.alive:
            continue
        if j.kind == "run" and f"_{pid}_" in j.run_id:
            return True
        if j.kind == "batch" and pid in (getattr(j, "prompts", None) or []):
            return True
    return False


def run_count(st, task: str, pid: str, index=None) -> dict:
    by, unreadable = index or _runs_by_prompt(st, task)
    runs = len(by.get(pid, [])) + sum(f"_{pid}_" in name for name in unreadable)
    return {"runs": runs, "pending": _pending(st, pid)}


def prompts_view(st, task: str) -> dict:
    store = prompt_store(st, task)
    rows = _store(st, store.rows)
    index = _runs_by_prompt(st, task)
    shares = board_kpis(st)["shares"]
    out = []
    for r in rows:
        own = [shares.get(x["run_id"], {}).get("overall") for x in index[0].get(r["prompt_id"], [])]
        scored = [v for v in own if isinstance(v, (int, float))]
        out.append({**r, **run_count(st, task, r["prompt_id"], index), "scored": len(scored),
                    "mean": sum(scored) / len(scored) if scored else None})
    return {"prompts": out, "next_id": _store(st, store.next_id, set(index[0])), "root_name": st.root.name}


def prompt_view(st, task: str, pid: str) -> dict:
    _need_prompt_id(pid)
    store = prompt_store(st, task)
    row = _store(st, store.get, pid)
    if row is None:
        raise UiError(404, f"No prompt {pid}")
    index = _runs_by_prompt(st, task)
    count = run_count(st, task, pid, index)
    shares = board_kpis(st)["shares"]
    mine = index[0].get(pid, [])
    runs = sorted(({"run_id": x["run_id"], "model": dashboard.model_key(x["run"]), "started_at": x["run"].get("started_at"),
                    "overall": core_scores(st, x["run_id"]), "cost": shares.get(x["run_id"], {}).get("spent")} for x in mine),
                  key=lambda r: str(r["started_at"] or ""), reverse=True)
    scored = [v for v in (shares.get(x["run_id"], {}).get("overall") for x in mine) if isinstance(v, (int, float))]
    return {"prompt": row, "runs_total": count["runs"], "pending": count["pending"],
            "next_version_id": _store(st, store.next_version_id, pid, set(index[0])),
            "locked": count["runs"] > 0 or count["pending"], "scored": len(scored),
            "mean": sum(scored) / len(scored) if scored else None, "runs": runs[:20]}


def core_scores(st, run_id: str):
    sc = _core()._scores(st.runs_dir / run_id) or {}
    v = sc.get("overall")
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _need_prompt_id(pid: str) -> None:
    from foundry.tasks import PROMPT_ID_RE

    if not PROMPT_ID_RE.match(pid) or ".." in pid:
        raise UiError(404, "No such prompt")


def tool_suggestions(st, task: str) -> list[str]:
    tools: set[str] = set()
    for e in _entries(st):
        for s in e["steps"]:
            if getattr(s, "type", None) == "tool_call" and isinstance(getattr(s, "name", None), str):
                tools.add(s.name)
                if isinstance(getattr(s, "server", None), str) and s.server:
                    tools.add(f"{s.server}/{s.name}")
    for r in _store(st, prompt_store(st, task).rows):
        for step in r["stages"] or []:
            tools.update(step)
    return sorted(tools)[:500]


def prompt_create(st, task: str, pid, text) -> str:
    store = prompt_store(st, task)
    index = _runs_by_prompt(st, task)
    taken = set(index[0]) | ({pid} if isinstance(pid, str) and run_count(st, task, pid, index)["runs"] else set())
    _store(st, store.create, pid, text, taken)
    return pid


def prompt_set_text(st, task: str, pid: str, text) -> None:
    _need_prompt_id(pid)
    store = prompt_store(st, task)
    with st.lock:  # no run may start between the check and the write
        count = run_count(st, task, pid)
        if count["runs"]:
            raise UiError(409, f"{pid} has {count['runs']} run{'s' if count['runs'] != 1 else ''}, so its text can't change. "
                               "Make a new version instead.")
        if count["pending"]:
            raise UiError(409, f"{pid} has a run in progress, so its text can't change. "
                               "Try again when it finishes, or make a new version.")
        _store(st, store.set_text, pid, text)


def prompt_new_version(st, task: str, pid: str) -> str:
    _need_prompt_id(pid)
    return _store(st, prompt_store(st, task).copy_version, pid, set(_runs_by_prompt(st, task)[0]))


def prompt_archive(st, task: str, pid: str, archived: bool) -> None:
    _need_prompt_id(pid)
    _store(st, prompt_store(st, task).set_archived, pid, bool(archived))


def prompt_set_stages(st, task: str, pid: str, stages) -> str:
    _need_prompt_id(pid)
    return _store(st, prompt_store(st, task).set_stages, pid, stages)
