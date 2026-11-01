"""Bringing outside traces in: importing a saved OTLP/JSON file, and the live OTLP receiver (start, stop, status)."""

from __future__ import annotations

from pathlib import Path

from foundry.config import ConfigError
from foundry.data.runs import is_valid_run_id
from foundry.engine import importer
from foundry.engine.importer import ImportError_, import_spans, spans_from_file
from foundry.engine.receiver import Receiver
from foundry.services import configs, state
from foundry.services.errors import ServiceError
from foundry.services.workspace import Workspace


def max_trace_bytes() -> int:
    """The trace upload size cap, read at call time (tests lower it)."""
    return importer.MAX_FILE_BYTES


def check_import_form(ws: Workspace, task: str, prompt_id: str, config: str) -> None:
    if task not in configs.task_ids(ws) or not is_valid_run_id(prompt_id) or (config and config not in configs.config_names(ws)):
        raise ServiceError(400, "Pick a task, a prompt id of letters/digits/._- and a known config.")


def _prices(ws: Workspace, config: str):
    return configs.load(ws, config).prices if config else None


def import_saved_trace(ws: Workspace, dest: Path, filename: str, task: str, prompt_id: str, config: str) -> list[Path]:
    state.of(ws).fresh_runs(ws)  # a run another instance imported must exist here too, or it would be overwritten
    try:
        prices = _prices(ws, config)  # before reading the trace, so a bad config is reported first, as before
        return import_spans(spans_from_file(dest), ws.runs_dir, task, prompt_id,
                            f"upload:{Path(filename or 'trace').name}", prices)
    except FileExistsError as exc:  # run ids come from the trace ids: the same trace imports to the same runs
        raise ServiceError(409, f"Already imported: run {Path(exc.filename).name} exists.") from None
    except (ImportError_, ConfigError) as exc:
        detail = str(exc).replace(str(dest), Path(filename or "trace").name)  # never show the server's upload path
        raise ServiceError(400, f"Could not import {filename}: {detail}") from None


def import_upload(ws: Workspace, filename: str, path: Path, task: str, prompt_id: str, config: str) -> list[Path]:
    """Import an uploaded trace (services.uploads.take gave its path); in the cloud the new runs are pushed to the
    store with their board cards, so every instance sees them."""
    check_import_form(ws, task, prompt_id, config)
    made = import_saved_trace(ws, path, filename, task, prompt_id, config)
    for d in made:
        state.of(ws).record_run(ws, d.name)
    return made


def receiver_start(ws: Workspace, task: str, prompt_id: str, config: str) -> None:
    if not ws.receiver_enabled:
        raise ServiceError(409, "Not available in this deployment; import trace files instead")
    check_import_form(ws, task, prompt_id, config)
    with ws.lock:
        if ws.receiver is None:
            try:
                ws.receiver = Receiver(ws.runs_dir, task, prompt_id, _prices(ws, config), port=ws.receiver_port,
                                       log=lambda *_: None).start()
            except OSError as exc:
                raise ServiceError(409, f"Cannot listen on 127.0.0.1:{ws.receiver_port}: {exc.strerror}. "
                                        "Is another receiver running?") from None


def receiver_stop(ws: Workspace) -> None:
    with ws.lock:
        if ws.receiver is not None:
            ws.receiver.close()
            ws.receiver = None


def receiver_status(ws: Workspace) -> dict:
    rec = ws.receiver
    port = getattr(rec, "port", None) or ws.receiver_port  # the port it actually bound (port 0 = any free one)
    written = list(getattr(rec, "written", []))
    return {"running": rec is not None, "port": port, "endpoint": f"http://127.0.0.1:{port}",
            "count": len(written), "received": [p.name for p in written][-20:]}
