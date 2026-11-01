"""AWS Lambda entry point for workers (aws-deploy design ADR-6): `{"job_id": ...}` from an async invoke runs that
job (services.workers). Dispatch only; the logic lives in services."""

from __future__ import annotations

_ctx = None


def context():
    """(workspace, store, mirror) for this execution environment, built once per cold start."""
    global _ctx
    if _ctx is None:
        from foundry import cloud_boot
        from foundry.services.workspace import Workspace

        store, mirror, backend = cloud_boot.cloud_context()
        ws = Workspace(cloud_boot.runs_dir(), cloud_boot.project_root(), 0, backend=backend)
        ws.cloud = (store, mirror)
        _ctx = (ws, store, mirror)
    return _ctx


def handler(event, context_):  # noqa: ARG001 (Lambda's signature)
    """`{"job_id"}` from the API's async invoke, or a batch workflow step: `{"kind": "batch_prepare" | "batch_item" |
    "batch_finalize", ...}` (aws-deploy design ADR-7)."""
    from foundry.services import workers

    ws, store, mirror = context()
    kind = event.get("kind")
    if kind == "batch_prepare":
        return workers.batch_prepare(ws, store, mirror, event["batch_job_id"])
    if kind == "batch_item":
        return workers.batch_item(ws, store, mirror, event["item"])
    if kind == "batch_finalize":
        error = event.get("error")
        return workers.batch_finalize(ws, store, mirror, event["batch_job_id"],
                                      error=(error.get("Cause") or error.get("Error")) if isinstance(error, dict) else error)
    return {"job_id": event["job_id"], "status": workers.run_job(ws, store, mirror, event["job_id"])}
