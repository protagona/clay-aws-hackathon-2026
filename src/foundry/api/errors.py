"""/api problems are always JSON {"error", "field"?} (A4): service errors with their status, bad bodies as 422."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from foundry.services.errors import ServiceError


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
