"""The local UI server (`foundry ui`): wires the layers together. Bound to 127.0.0.1 only.

    foundry.api.guard    Host/Origin checks, security headers, the CSRF token
    foundry.api.routes   the JSON API, calling foundry.services
    foundry.ui.spa       the built TypeScript app

Every request's Host is checked (DNS rebinding); every write needs a same-origin Origin (when sent) and the
per-process CSRF token. At most one UI-started run at a time.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from foundry.api import errors, guard
from foundry.api.routes import make_router
from foundry.services.cloud_state import CloudState
from foundry.services.workspace import Workspace
from foundry.ui import spa


def create_app(runs_dir: str | Path, project_root: str | Path, port: int = 8765, receiver_port: int = 4318,
               cloud: tuple | None = None, access=None, cap_usd: float | None = None) -> FastAPI:
    """`cloud` = (store, mirror, job backend) when state lives in the cloud store (aws-deploy); None on a laptop."""
    ws = Workspace(Path(runs_dir).resolve(), Path(project_root).resolve(), port, receiver_port=receiver_port)
    if cloud is not None:
        store, mirror, backend = cloud
        ws.cloud, ws.backend = (store, mirror), backend
        ws.state = CloudState(store, mirror)
        ws.receiver_enabled = False
    ws.cap_usd = cap_usd
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ui = ws
    guard.install(app, port, access, upload_origin=cloud[0].upload_origin if cloud is not None else None,
                  build=os.environ.get("FOUNDRY_BUILD", ""))
    errors.install(app)
    app.include_router(make_router(ws, guard.new_csrf_token(), access))
    spa.mount(app)
    return app
