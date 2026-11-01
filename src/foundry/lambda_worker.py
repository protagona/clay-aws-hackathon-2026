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
    from foundry.services.workers import run_job

    ws, store, mirror = context()
    return {"job_id": event["job_id"], "status": run_job(ws, store, mirror, event["job_id"])}
