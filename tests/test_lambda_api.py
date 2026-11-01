"""The function-URL adapter (aws-deploy slice 1, ADR-3): payload format 2.0 in, the same app, payload 2.0 out."""

import asyncio
import base64
import json

from conftest import ROOT

from foundry.lambda_api import run_asgi
from foundry.server import create_app

DOMAIN = "abc123.lambda-url.us-west-2.on.aws"


def event(method="GET", path="/api/session", query="", headers=None, cookies=None, body=None, b64=False, ip="203.0.113.7"):
    """A payload 2.0 event shaped like the docs' example (lambda/latest/dg/urls-invocation.html)."""
    e = {"version": "2.0", "routeKey": "$default", "rawPath": path, "rawQueryString": query,
         "headers": {"host": DOMAIN, **(headers or {})}, "isBase64Encoded": b64,
         "requestContext": {"domainName": DOMAIN, "http": {"method": method, "path": path, "protocol": "HTTP/1.1",
                                                          "sourceIp": ip, "userAgent": "test"}}}
    if cookies:
        e["cookies"] = cookies
    if body is not None:
        e["body"] = body
    return e


def call(app, e):
    return asyncio.run(run_asgi(app, e))


def test_healthz_ignores_host(tmp_path):
    app = create_app(tmp_path / "runs", ROOT)
    r = call(app, event(path="/healthz", headers={"host": "anything.example"}))
    assert r["statusCode"] == 200 and r["body"] == "ok"


def test_function_url_host_is_the_allowed_host(tmp_path):
    app = create_app(tmp_path / "runs", ROOT)
    r = call(app, event())
    assert r["statusCode"] == 200 and json.loads(r["body"])["csrf"]
    assert call(app, event(headers={"host": "evil.example"}))["statusCode"] == 400
    assert call(app, event(headers={"host": "127.0.0.1:8765"}))["statusCode"] == 400  # loopback isn't the cloud host


def test_writes_need_the_https_origin_of_the_domain(tmp_path):
    app = create_app(tmp_path / "runs", ROOT)
    csrf = json.loads(call(app, event())["body"])["csrf"]
    h = {"x-csrf-token": csrf, "content-type": "application/json"}
    body = json.dumps({"prompt_id": "adb-001", "config": "nope"})
    evil = call(app, event("POST", "/api/runs", headers={**h, "origin": "https://evil.example"}, body=body))
    assert evil["statusCode"] == 403
    ok = call(app, event("POST", "/api/runs", headers={**h, "origin": f"https://{DOMAIN}"}, body=body))
    assert ok["statusCode"] == 400 and "Unknown config" in json.loads(ok["body"])["error"]  # past the guard


def test_query_cookies_and_base64_bodies(tmp_path):
    app = create_app(tmp_path / "runs", ROOT)
    r = call(app, event(path="/api/dashboard", query="rubric=all", cookies=["a=1", "b=2"]))
    assert r["statusCode"] == 200 and r["headers"]["content-type"] == "application/json" and not r["isBase64Encoded"]
    csrf = json.loads(call(app, event())["body"])["csrf"]
    raw = json.dumps({"prompt_id": "adb-001", "config": "nope"}).encode()
    r = call(app, event("POST", "/api/runs", headers={"x-csrf-token": csrf, "content-type": "application/json"},
                        body=base64.b64encode(raw).decode(), b64=True))
    assert r["statusCode"] == 400


def test_binary_responses_are_base64_and_security_headers_kept(tmp_path):
    app = create_app(tmp_path / "runs", ROOT)
    r = call(app, event(path="/"))
    assert r["statusCode"] in (200, 503) and "content-security-policy" in r["headers"]
    from foundry.lambda_api import lambda_response
    out = lambda_response(200, [(b"content-type", b"image/png"), (b"set-cookie", b"a=1; Path=/"),
                                (b"set-cookie", b"b=2; Path=/"), (b"x-a", b"1"), (b"x-a", b"2")], b"\\x89PNG")
    assert out["isBase64Encoded"] and base64.b64decode(out["body"]) == b"\\x89PNG"
    assert out["cookies"] == ["a=1; Path=/", "b=2; Path=/"] and out["headers"]["x-a"] == "1, 2"


def test_source_ip_reaches_the_app_as_the_client(tmp_path):
    from foundry.lambda_api import asgi_scope
    s = asgi_scope(event(ip="2001:db8::1"))
    assert s["client"] == ("2001:db8::1", 0) and s["extensions"]["foundry_lambda"]["domain"] == DOMAIN


def test_streamed_file_responses_arrive_whole(tmp_path):  # spike S-1 found assets served as 0 bytes
    from foundry.ui.spa import DIST

    app = create_app(tmp_path / "runs", ROOT)
    asset = next((DIST / "assets").glob("*.js"))
    r = call(app, event(path=f"/assets/{asset.name}"))
    assert r["statusCode"] == 200 and r["body"].encode() == asset.read_bytes()


def test_the_deployed_app_has_sign_in_the_cap_and_its_build_id(tmp_path, monkeypatch):  # aws-deploy slice 9
    from fakestore import FakeStore

    from foundry import cloud_boot, lambda_api
    from foundry.data.mirror import Mirror
    from foundry.services.cloud_jobs import CloudBackend

    store = FakeStore()
    monkeypatch.setenv("STATE_BUCKET", "b")
    monkeypatch.setenv("MONTHLY_CAP_USD", "30")
    monkeypatch.setenv("FOUNDRY_BUILD", "abc1234")
    monkeypatch.setenv("FOUNDRY_HOME", str(tmp_path / "project"))
    monkeypatch.setenv("FOUNDRY_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("FOUNDRY_APP_DIR", str(ROOT))
    monkeypatch.setattr(cloud_boot, "cloud_context", lambda: (store, Mirror(store, tmp_path), CloudBackend(store)))
    app = lambda_api.build_app()
    assert app.state.ui.cap_usd == 30.0 and app.state.ui.receiver_enabled is False
    assert call(app, event(path="/healthz"))["body"] == "ok abc1234"
    r = call(app, event())
    assert r["statusCode"] == 401 and json.loads(r["body"]) == {"error": "Sign in required"}
    monkeypatch.setenv("MONTHLY_CAP_USD", "")
    assert cloud_boot.cap_usd() is None


def test_batches_start_one_named_execution_and_the_worker_answers_a_health_check(monkeypatch):
    from foundry import lambda_worker
    from foundry.data.store import BatchWorkflow

    class Sfn:
        class exceptions:
            ExecutionAlreadyExists = type("ExecutionAlreadyExists", (Exception,), {})

        def __init__(self):
            self.names = []

        def start_execution(self, stateMachineArn, name, input):
            if name in self.names:
                raise self.exceptions.ExecutionAlreadyExists()
            self.names.append(name)
            assert json.loads(input) == {"batch_job_id": "j1"}
            return {"executionArn": f"arn:aws:states:us-west-2:1:execution:Batch:{name}"}

        def describe_execution(self, executionArn):
            return {"status": "RUNNING"}

    flow = BatchWorkflow.__new__(BatchWorkflow)
    flow.arn, flow.client = "arn:aws:states:us-west-2:1:stateMachine:Batch", Sfn()
    first = flow.start("j1")
    assert flow.start("j1") == first == "arn:aws:states:us-west-2:1:execution:Batch:batch-j1"  # a retried start
    assert flow.describe(first) == "RUNNING"
    monkeypatch.setattr(lambda_worker, "context", lambda: (None, None, None))
    monkeypatch.setenv("FOUNDRY_BUILD", "abc1234")
    assert lambda_worker.handler({"kind": "healthz"}, None) == {"ok": True, "build": "abc1234"}
