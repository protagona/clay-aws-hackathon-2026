"""AWS Lambda entry point for workers (aws-deploy design ADR-6): `{"job_id": ...}` from an async invoke runs that
job (services.workers). Dispatch only; the logic lives in services."""

from __future__ import annotations

_ctx = None


def context():
    """(workspace, store, mirror) for this execution environment, built once per cold start."""
    global _ctx
    if _ctx is None:
        from foundry import cloud_boot
        from foundry.services.cloud_state import CloudState
        from foundry.services.workspace import Workspace

        store, mirror, backend = cloud_boot.cloud_context()
        ws = Workspace(cloud_boot.runs_dir(), cloud_boot.project_root(), 0, backend=backend)
        ws.cloud = (store, mirror)
        ws.state = CloudState(store, mirror)
        _ctx = (ws, store, mirror)
    return _ctx


def _healthz(ws, store, mirror, event):  # noqa: ARG001 (cloud.sh's pre-switch check of a new version)
    import os

    return {"ok": True, "build": os.environ.get("FOUNDRY_BUILD", "")}


def _batch_finalize(ws, store, mirror, event):
    from foundry.services import workers

    error = event.get("error")
    return workers.batch_finalize(ws, store, mirror, event["batch_job_id"],
                                  error=(error.get("Cause") or error.get("Error")) if isinstance(error, dict) else error)


def _events():
    """Worker events by kind (a separate namespace from job kinds, services/job_kinds.py): who sends each one."""
    from foundry.services import workers

    return {
        "healthz": _healthz,  # scripts/cloud.sh deploy
        "batch_prepare": lambda ws, store, mirror, e: workers.batch_prepare(ws, store, mirror, e["batch_job_id"]),
        "batch_item": lambda ws, store, mirror, e: workers.batch_item(ws, store, mirror, e["item"]),  # Step Functions
        "batch_finalize": _batch_finalize,  # Step Functions, also on its error path
        "reindex": lambda ws, store, mirror, e: workers.reindex(ws, store, mirror, after=e.get("after"),
                                                               budget_s=float(e.get("budget_s") or 600)),  # cloud_ops
    }


EVENTS = ("healthz", "batch_prepare", "batch_item", "batch_finalize", "reindex")


def handler(event, context_):  # noqa: ARG001 (Lambda's signature)
    """`{"job_id"}` from the API's async invoke, or `{"kind": <one of EVENTS>, ...}` (a batch workflow step, a
    deploy's health check, a re-index)."""
    from foundry.services import workers

    ws, store, mirror = context()
    kind = event.get("kind")
    if kind is not None:
        return _events()[kind](ws, store, mirror, event)
    return {"job_id": event["job_id"], "status": workers.run_job(ws, store, mirror, event["job_id"])}
