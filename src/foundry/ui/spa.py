"""The built TypeScript app (web/ -> src/foundry/ui/web_dist): /assets/* as immutable files, and every other non-API
path falls back to index.html, where the app's router takes over. Imports nothing from foundry."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

DIST = Path(__file__).with_name("web_dist")


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

    @app.get("/{rest:path}", response_class=HTMLResponse)
    def spa(rest: str):
        index = DIST / "index.html"
        if not index.is_file():
            return HTMLResponse("<!doctype html><title>Foundry</title><p>The UI bundle isn't built. Run "
                                "<code>cd web && npm ci && npm run build</code>.", status_code=503)
        return HTMLResponse(index.read_text(), headers={"Cache-Control": "no-cache"})
