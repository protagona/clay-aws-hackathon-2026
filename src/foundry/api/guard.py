"""Request checks for a server bound to 127.0.0.1: every request's Host (DNS rebinding), a same-origin Origin on
writes, security headers on every response, and the per-process CSRF token that every /api write must carry."""

from __future__ import annotations

import hmac
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from foundry.services.errors import ServiceError

CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
       "img-src 'self' data:; font-src 'self'; form-action 'self'; frame-ancestors 'none'")
MULTIPART_PATHS = ("/api/import", "/api/rubric/drafts")


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def install(app: FastAPI, port: int) -> None:
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        api = request.url.path.startswith("/api/")

        def refuse(status: int, message: str) -> Response:
            return JSONResponse({"error": message}, status_code=status) if api else PlainTextResponse(message, status_code=status)

        # Behind a Lambda function URL, AWS states the public host (requestContext.domainName; never the client's
        # choice): it is the one allowed Host, and https://<it> the one allowed Origin (aws-deploy design ADR-3).
        domain = request.scope.get("extensions", {}).get("foundry_lambda", {}).get("domain")
        host = request.headers.get("host", "")
        origin = f"https://{domain}" if domain else f"http://{host}"
        if request.method == "GET" and request.url.path == "/healthz":
            response = PlainTextResponse("ok")  # before every check: health probes don't send our Host
        elif not (host == domain if domain else host in allowed_hosts):
            response = refuse(400, "Bad Host header")
        elif request.method in ("POST", "PUT", "PATCH", "DELETE") and (request.headers.get("origin") or origin) != origin:
            response = refuse(403, "Cross-origin request refused")
        else:
            try:
                response = await call_next(request)
            except Exception:
                response = refuse(500, "Internal Server Error")
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Not "no-referrer": with it, browsers send `Origin: null` on form POSTs (Fetch spec), which the Origin
        # check above rejects. "same-origin" still sends nothing to other sites.
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        return response


def write_check(csrf: str):
    """A dependency for every /api write: the X-CSRF-Token header (a custom header cannot be sent cross-site without
    a CORS preflight, which is never granted) and a typed body."""

    def require_write(request: Request) -> None:
        token = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(token.encode(), csrf.encode()):
            raise ServiceError(403, "Missing or invalid CSRF token; reload the page")
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        multipart = request.url.path in MULTIPART_PATHS
        if request.headers.get("content-length", "0") not in ("", "0") or ctype:
            allowed = ("multipart/form-data", "application/x-www-form-urlencoded") if multipart else ("application/json",)
            if ctype not in allowed:  # the CSRF header above is what stops cross-site writes; this keeps bodies typed
                raise ServiceError(415, "multipart/form-data required" if multipart else "Content-Type must be application/json")

    return require_write
