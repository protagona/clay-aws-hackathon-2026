"""AWS Lambda entry point for the API and web app: a function-URL request (payload format 2.0) becomes an ASGI
request to the FastAPI app, and its response goes back the same way (aws-deploy design ADR-3).

No third-party adapter: Lambda Web Adapter is an unsupported awslabs project and Mangum a third-party package, and
the format is small (docs: lambda/latest/dg/urls-invocation.html). Function URLs buffer bodies (6 MB each way).

The client address (`requestContext.http.sourceIp`) and the public host (`requestContext.domainName`) come from
AWS, not the client; the app sees them as the ASGI client and as `scope["extensions"]["foundry_lambda"]`.
"""

from __future__ import annotations

import asyncio
import base64

TEXT_TYPES = ("text/", "application/json", "application/javascript", "image/svg+xml", "application/xml")

_app = None


def asgi_scope(event: dict) -> dict:
    """The ASGI HTTP scope for a function-URL event."""
    http = event["requestContext"]["http"]
    headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (event.get("headers") or {}).items()]
    if event.get("cookies"):  # payload 2.0 moves Cookie headers into an array
        headers.append((b"cookie", "; ".join(event["cookies"]).encode("latin-1")))
    domain = event["requestContext"].get("domainName", "")
    return {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
        "method": http["method"], "scheme": "https", "path": event.get("rawPath") or "/",
        "raw_path": (event.get("rawPath") or "/").encode(), "query_string": (event.get("rawQueryString") or "").encode(),
        "root_path": "", "headers": headers, "client": (http.get("sourceIp", ""), 0), "server": (domain, 443),
        "extensions": {"foundry_lambda": {"domain": domain}},
    }


def request_body(event: dict) -> bytes:
    body = event.get("body") or ""
    return base64.b64decode(body) if event.get("isBase64Encoded") else body.encode()


async def run_asgi(app, event: dict) -> dict:
    """One request through `app`, returned in the function-URL response shape."""
    scope = asgi_scope(event)
    body = request_body(event)
    sent = False
    status, headers, chunks = 500, [], []

    async def receive():
        nonlocal sent
        if sent:
            # The client stays connected until the response is complete (as with a real server). Answering
            # "disconnect" here made Starlette's FileResponse, which listens for disconnects, abort mid-file.
            await asyncio.Event().wait()
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        nonlocal status, headers
        if message["type"] == "http.response.start":
            status, headers = message["status"], message.get("headers", [])
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    return lambda_response(status, headers, b"".join(chunks))


def lambda_response(status: int, raw_headers: list, body: bytes) -> dict:
    out: dict[str, str] = {}
    cookies: list[str] = []
    for k, v in raw_headers:
        name, value = k.decode("latin-1").lower(), v.decode("latin-1")
        if name == "set-cookie":
            cookies.append(value)  # payload 2.0 returns cookies separately; joining Set-Cookie would break them
        else:
            out[name] = f"{out[name]}, {value}" if name in out else value
    ctype = out.get("content-type", "")
    text = ctype.startswith(TEXT_TYPES)
    resp = {"statusCode": status, "headers": out, "isBase64Encoded": not text,
            "body": body.decode("utf-8", "replace") if text else base64.b64encode(body).decode()}
    if cookies:
        resp["cookies"] = cookies
    return resp


def build_app():
    """The app this function serves: the same FastAPI app as `foundry ui`, on /tmp state (cloud_boot wires it)."""
    from foundry import cloud_boot
    from foundry.server import create_app

    return create_app(cloud_boot.runs_dir(), cloud_boot.project_root())


def handler(event, context):  # noqa: ARG001 (Lambda's signature)
    global _app
    if _app is None:
        _app = build_app()
    return asyncio.run(run_asgi(_app, event))
