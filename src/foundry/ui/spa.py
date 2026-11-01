"""The built TypeScript app (web/ -> src/foundry/ui/web_dist): /assets/* as immutable files, and every other non-API
path falls back to index.html, where the app's router takes over. Also the remote CLI as a zipapp. Imports nothing
from foundry."""

from __future__ import annotations

import functools
import io
import zipfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

DIST = Path(__file__).with_name("web_dist")
CLI_SOURCE = Path(__file__).parents[1] / "cli_remote.py"  # read as a file, not imported


@functools.cache
def cli_pyz() -> bytes:
    """foundry-remote.pyz: cli_remote.py as a zipapp (`python3 foundry-remote.pyz ...`), built from the installed
    source so it can't go stale. Fixed timestamps and modes: the same source gives the same bytes."""
    buf = io.BytesIO()
    buf.write(b"#!/usr/bin/env python3\n")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in (("cli_remote.py", CLI_SOURCE.read_bytes()),
                           ("__main__.py", b"import sys\nfrom cli_remote import main\nsys.exit(main())\n")):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return buf.getvalue()


def mount(app: FastAPI) -> None:
    """Call after the API router is included: the catch-all here would otherwise answer /api paths."""

    @app.get("/assets/{name:path}")
    def assets(name: str):
        try:
            f = (DIST / "assets" / name).resolve()
        except ValueError:  # a NUL byte in the path (/assets/%00) raised a 500 (increment 16 sunset gate)
            return JSONResponse({"error": "Not found"}, status_code=404)
        if not f.is_relative_to((DIST / "assets").resolve()) or not f.is_file():
            return JSONResponse({"error": "Not found"}, status_code=404)
        return FileResponse(f, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/cli/{name}")
    def cli(name: str):
        """The remote CLI, one standard-library Python program (aws-deploy spec I3). Public: it holds no secrets;
        using it needs a token."""
        if name != "foundry-remote.pyz":
            return JSONResponse({"error": "Not found"}, status_code=404)
        return Response(cli_pyz(), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-cache"})

    @app.get("/{rest:path}", response_class=HTMLResponse)
    def spa(rest: str):
        index = DIST / "index.html"
        if not index.is_file():
            return HTMLResponse("<!doctype html><title>Foundry</title><p>The UI bundle isn't built. Run "
                                "<code>cd web && npm ci && npm run build</code>.", status_code=503)
        return HTMLResponse(index.read_text(), headers={"Cache-Control": "no-cache"})
