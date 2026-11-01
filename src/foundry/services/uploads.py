"""Two-step uploads (aws-deploy spec I6, design ADR-10): ask for a ticket, send the file where it says, then name
the upload in the import or draft call. In the cloud the ticket is a presigned S3 POST (a function URL takes at
most 6 MB per request); locally it's a route on this server."""

from __future__ import annotations

import secrets
from pathlib import Path

from foundry.data import uploads as data_uploads
from foundry.services import imports, rubrics, state
from foundry.services.errors import ServiceError
from foundry.services.workspace import Workspace


def max_bytes(kind: str) -> int:
    if kind == "trace":
        return imports.max_trace_bytes()
    if kind == "rubric_source":
        return rubrics.max_source_bytes()
    raise ServiceError(400, "Unknown upload kind")


def ticket(ws: Workspace, kind: str, filename: str, size: int) -> dict:
    limit = max_bytes(kind)
    if size > limit:
        raise ServiceError(413, f"The file is over the {limit / (1 << 20):.0f} MB limit.")
    meta = {"kind": kind, "filename": Path(filename).name[:200], "max_bytes": limit}
    return state.of(ws).upload_ticket(ws, secrets.token_hex(8), meta)


def local_target(ws: Workspace, upload_id: str) -> tuple[Path, int]:
    """Where a laptop's upload route writes this upload, and its size cap (404 for an unknown or cloud ticket)."""
    return state.of(ws).upload_local_target(ws, upload_id)


def take(ws: Workspace, upload_id: str, kind: str) -> tuple[str, Path]:
    """(original file name, a local path to its bytes) for an upload of `kind`; 404 if there is none."""
    if not data_uploads.UPLOAD_ID_RE.match(upload_id or ""):
        raise ServiceError(404, "Unknown upload")
    return state.of(ws).upload_take(ws, upload_id, kind)
