"""/api problems are always JSON {"error", "field"?} (A4): service errors with their status, bad bodies as 422, and
a response that breaks its contract (foundry.api.schemas) as a logged 500."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse

from foundry.services.errors import ServiceError

log = logging.getLogger("foundry.api")


def error(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


def install(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError):
        return error(exc.status, exc.message, **({"field": exc.field} if exc.field else {}))

    @app.exception_handler(RequestValidationError)
    async def bad_body(request: Request, exc: RequestValidationError):
        first = (exc.errors() or [{}])[0]
        where = ".".join(str(p) for p in first.get("loc", []) if p != "body") or "request"
        return error(422, f"{where}: {first.get('msg', 'invalid')}")

    @app.exception_handler(ResponseValidationError)
    async def broken_contract(request: Request, exc: ResponseValidationError):
        # Where it broke, never the values (they may be run text): enough to find the model or service to fix.
        where = "; ".join(f"{'.'.join(str(p) for p in e.get('loc', ()))} {e.get('type', '?')}" for e in exc.errors()[:5])
        log.error("response breaks its contract: %s %s: %s", request.method, request.url.path, where)
        return error(500, "Internal Server Error")
