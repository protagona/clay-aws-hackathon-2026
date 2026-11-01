"""Request checks for a server bound to 127.0.0.1: every request's Host (DNS rebinding), a same-origin Origin on
writes, security headers on every response, and the per-process CSRF token that every /api write must carry."""

from __future__ import annotations

import hmac
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from foundry.services.errors import ServiceError

CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'{connect}; "
       "img-src 'self' data:; font-src 'self'; form-action 'self'; frame-ancestors 'none'")
MULTIPART_PATHS = ("/api/import", "/api/rubric/drafts")


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


PUBLIC_API = ("/api/auth/login",)  # the only /api path that needs no credentials (aws-deploy ADR-9)


def authenticate(request: Request, access) -> None:
    """Behind a deployment with auth on: who is calling, from the X-Foundry-Token header or the session cookie.
    Sets request.state.auth = (how, name); raises ServiceError 401/429/500."""
    from foundry.services.access import COOKIE

    token = request.headers.get("x-foundry-token")
    if token:
        name = access.token_name(token)
        if name is None:
            access.limit(request.client.host if request.client else "")
            raise ServiceError(401, "Sign in required")
        request.state.auth = ("header", name)
        return
    cookie = request.cookies.get(COOKIE)
    name = access.session_name(cookie) if cookie else None
    if name is None:
        raise ServiceError(401, "Sign in required")
    request.state.auth = ("cookie", name)


def install(app: FastAPI, port: int, access=None, upload_origin: str | None = None, build: str = "") -> None:
    """`upload_origin`: the state bucket's https origin, the one other place the page may send to (presigned
    upload POSTs, aws-deploy ADR-10). `build`: the deployed build id, which /healthz states (aws-deploy D5)."""
    csp = CSP.format(connect=f" {upload_origin}" if upload_origin else "")
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
            response = PlainTextResponse(f"ok {build}" if build else "ok")  # before every check: health probes don't send our Host
        elif not (host == domain if domain else host in allowed_hosts):
            response = refuse(400, "Bad Host header")
        elif request.method in ("POST", "PUT", "PATCH", "DELETE") and (request.headers.get("origin") or origin) != origin:
            response = refuse(403, "Cross-origin request refused")
        else:
            response = None
            if access is not None and api and request.url.path not in PUBLIC_API:
                try:
                    authenticate(request, access)
                except ServiceError as exc:
                    response = refuse(exc.status, exc.message)
            if response is None:
                try:
                    response = await call_next(request)
                except Exception:
                    response = refuse(500, "Internal Server Error")
        response.headers["Content-Security-Policy"] = csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Not "no-referrer": with it, browsers send `Origin: null` on form POSTs (Fetch spec), which the Origin
        # check above rejects. "same-origin" still sends nothing to other sites.
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        return response


def expected_csrf(request: Request, csrf: str, access) -> str | None:
    """The CSRF token this request must carry: none for X-Foundry-Token callers (no ambient credential), one derived
    from the session cookie behind auth (any instance can check it), else this process's token (a laptop)."""
    from foundry.services.access import COOKIE

    how = getattr(request.state, "auth", (None,))[0]
    if how == "header":
        return None
    if how == "cookie" and access is not None:
        return access.csrf_for(request.cookies.get(COOKIE, ""))
    return csrf


def write_check(csrf: str, access=None, check_csrf: bool = True):
    """A dependency for every /api write: the X-CSRF-Token header (a custom header cannot be sent cross-site without
    a CORS preflight, which is never granted) and a typed body. Sign-in skips the CSRF part (there's no session yet;
    the Origin check and the JSON body still apply)."""

    def require_write(request: Request) -> None:
        expected = expected_csrf(request, csrf, access) if check_csrf else None
        token = request.headers.get("x-csrf-token", "")
        if expected is not None and not hmac.compare_digest(token.encode(), expected.encode()):
            raise ServiceError(403, "Missing or invalid CSRF token; reload the page")
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        multipart = request.url.path in MULTIPART_PATHS or (request.url.path.startswith("/api/uploads/")
                                                            and request.url.path != "/api/uploads/")
        if request.headers.get("content-length", "0") not in ("", "0") or ctype:
            allowed = ("multipart/form-data", "application/x-www-form-urlencoded") if multipart else ("application/json",)
            if ctype not in allowed:  # the CSRF header above is what stops cross-site writes; this keeps bodies typed
                raise ServiceError(415, "multipart/form-data required" if multipart else "Content-Type must be application/json")

    return require_write
