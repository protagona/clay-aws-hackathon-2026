"""Uploads streamed under their size caps: a trace file (saved to <runs dir>/.uploads for the importer) and a rubric
source (kept in memory). The one api module allowed to write files (tests/test_architecture.py)."""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import UploadFile

from foundry.services import imports, rubrics
from foundry.services.errors import ServiceError
from foundry.services.workspace import Workspace


async def save_upload(ws: Workspace, file: UploadFile) -> Path:
    """A trace upload streamed to runs/.uploads/ under the import size cap."""
    limit = imports.max_trace_bytes()
    upload_dir = ws.runs_dir / ".uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{secrets.token_hex(8)}.json"
    size = 0
    with open(dest, "wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                out.close()
                dest.unlink(missing_ok=True)
                raise ServiceError(413, f"File is over the {limit // (1 << 20)} MB limit.")
            out.write(chunk)
    return dest


async def read_rubric_upload(file: UploadFile | None) -> tuple[str, bytes]:
    """(file name, bytes) of an optional rubric upload, under the rubric source size cap."""
    if file is None or not file.filename:
        return "", b""
    limit = rubrics.max_source_bytes()
    chunks, size = [], 0
    while chunk := await file.read(1 << 20):
        size += len(chunk)
        if size > limit:
            raise ServiceError(413, f"The file is over the {limit / 1e6:.1f} MB limit.")
        chunks.append(chunk)
    return file.filename, b"".join(chunks)
