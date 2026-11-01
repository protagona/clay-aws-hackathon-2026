"""Two-step uploads (aws-deploy spec I6, design ADR-10): ask for a ticket, send the file where it says, then name
the upload in the import or draft call. In the cloud the ticket is a presigned S3 POST (a function URL takes at
most 6 MB per request); locally it's a route on this server."""

from __future__ import annotations

import secrets
import time
from pathlib import Path

from foundry.data import uploads as data_uploads
from foundry.data.store import Put
from foundry.services import cloud_state, imports, rubrics
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
    upload_id = secrets.token_hex(8)
    meta = {"kind": kind, "filename": Path(filename).name[:200], "max_bytes": limit}
    if cloud_state.on(ws):
        store, _ = ws.cloud
        store.write(Put({"pk": "UPLOAD", "sk": upload_id, **meta, "ttl": int(time.time()) + 86400}))
        post = store.presign_post(f"uploads/{upload_id}", limit)
        return {"upload_id": upload_id, "url": post["url"], "fields": post["fields"]}
    data_uploads.write_meta(ws.runs_dir, upload_id, meta)
    return {"upload_id": upload_id, "url": f"/api/uploads/{upload_id}", "fields": {}}


def local_target(ws: Workspace, upload_id: str) -> tuple[Path, int]:
    """Where a laptop's upload route writes this upload, and its size cap (404 for an unknown or cloud ticket)."""
    meta = data_uploads.read_meta(ws.runs_dir, upload_id) if data_uploads.UPLOAD_ID_RE.match(upload_id) else None
    if meta is None or cloud_state.on(ws):
        raise ServiceError(404, "Unknown upload")
    return data_uploads.path(ws.runs_dir, upload_id), int(meta["max_bytes"])


def take(ws: Workspace, upload_id: str, kind: str) -> tuple[str, Path]:
    """(original file name, a local path to its bytes) for an upload of `kind`; 404 if there is none."""
    if not data_uploads.UPLOAD_ID_RE.match(upload_id or ""):
        raise ServiceError(404, "Unknown upload")
    if cloud_state.on(ws):
        store, _ = ws.cloud
        meta = store.get_item("UPLOAD", upload_id)
        if meta is None or meta.get("kind") != kind:
            raise ServiceError(404, "Unknown upload")
        size = store.object_size(f"uploads/{upload_id}")
        if size is None:
            raise ServiceError(400, "The upload hasn't arrived; send the file first.")
        if size > int(meta["max_bytes"]):
            raise ServiceError(413, "The file is over the limit.")
        return meta["filename"], data_uploads.save_bytes(ws.runs_dir, upload_id,
                                                          store.get_object(f"uploads/{upload_id}"))
    meta = data_uploads.read_meta(ws.runs_dir, upload_id)
    if meta is None or meta.get("kind") != kind or data_uploads.size(ws.runs_dir, upload_id) is None:
        raise ServiceError(404, "Unknown upload")
    return meta["filename"], data_uploads.path(ws.runs_dir, upload_id)
