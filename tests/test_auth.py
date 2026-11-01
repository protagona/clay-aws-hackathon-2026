"""Access without AWS identity (aws-deploy slice 6a, spec A1–A2, A6, design ADR-9): tokens, sessions, stateless
CSRF, the rate limit, and failing closed. Two API instances share one fake store."""

import re

import pytest
from conftest import ROOT
from fakestore import FakeStore
from fastapi.testclient import TestClient
from test_cloud_api import project
from test_cloud_jobs import Clock

from foundry import cloud_boot
from foundry.data.mirror import Mirror
from foundry.data.store import Delete, Put
from foundry.data.tokens import TokenStore
from foundry.server import create_app
from foundry.services.access import Access, client_key, sha256
from foundry.services.cloud_jobs import CloudBackend

BASE = "http://127.0.0.1:8765"
TOKEN = "fdy_" + "a" * 43


@pytest.fixture
def deploy(tmp_path):
    store, clock = FakeStore(), Clock()
    cloud_boot.seed_projects(store, Mirror(store, tmp_path / "seed"), app=ROOT)
    store.write(Put({"pk": "TOKEN", "sk": "alice", "sha256": sha256(TOKEN)}))
    store.write(Put({"pk": "CONFIG", "sk": "session_key", "key": "k" * 64}))

    def api(name):
        root = project(tmp_path, name)
        mirror = Mirror(store, root.parent)
        for it in store.query("TREE", prefix="project#"):
            mirror.pull(it["sk"], item=it)
        app = create_app(root.parent / "runs", root, cloud=(store, mirror, CloudBackend(store, clock=clock)),
                         access=Access(TokenStore(store, clock=clock), clock=clock))
        return TestClient(app, base_url=BASE)

    return store, clock, api("a"), api("b")


def login(c, token=TOKEN):
    r = c.post("/api/auth/login", json={"token": token})
    cookie = r.headers.get("set-cookie", "")
    m = re.search(r"fdy_session=([^;]+)", cookie)
    return r, (m.group(1) if m else None), cookie


def test_every_api_route_needs_credentials(deploy):
    from foundry.api.routes import make_router
    from foundry.services.workspace import Workspace

    _, _, a, _ = deploy
    for route in make_router(Workspace(ROOT, ROOT, 0), "x", access=object()).routes:
        path = re.sub(r"\{[^}]+\}", "x", route.path)
        for method in route.methods:
            if path in ("/api/auth/login",):
                continue
            r = a.request(method, path, headers={"content-type": "application/json"})
            assert r.status_code == 401 and r.json() == {"error": "Sign in required"}, (method, path, r.status_code)
    assert a.get("/healthz").text == "ok" and a.get("/").status_code in (200, 503)  # public: the shell, no data


def test_header_token_works_without_csrf_and_wrong_ones_are_limited(deploy):
    _, _, a, _ = deploy
    h = {"x-foundry-token": TOKEN}
    assert a.get("/api/auth/me", headers=h).json() == {"name": "alice"}
    r = a.post("/api/runs", headers={**h, "content-type": "application/json"}, json={"prompt_id": "adb-001", "config": "nope"})
    assert r.status_code == 400  # past auth and CSRF (none needed for header tokens), refused by the service
    codes = [a.get("/api/runs", headers={"x-foundry-token": "fdy_wrong"}).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_browser_session_with_stateless_csrf_across_instances(deploy):
    _, _, a, b = deploy
    r, cookie, raw = login(a)
    assert r.status_code == 200 and r.json() == {"name": "alice"}
    assert all(x in raw for x in ("HttpOnly", "Secure", "SameSite=strict", "Path=/")), raw
    hc = {"cookie": f"fdy_session={cookie}"}
    session = b.get("/api/session", headers=hc).json()  # another instance knows the session and its CSRF token
    assert session["user"] == "alice" and session["csrf"]
    body = {"prompt_id": "adb-001", "config": "nope"}
    assert a.post("/api/runs", headers=hc, json=body).status_code == 403  # no CSRF token
    assert a.post("/api/runs", headers={**hc, "x-csrf-token": session["csrf"]}, json=body).status_code == 400


def test_revocation_ends_tokens_and_sessions_within_the_cache_window(deploy):
    store, clock, a, _ = deploy
    _, cookie, _ = login(a)
    hc, h = {"cookie": f"fdy_session={cookie}"}, {"x-foundry-token": TOKEN}
    store.write(Delete("TOKEN", "alice"))
    clock.t += 61
    assert a.get("/api/runs", headers=h).status_code == 401 and a.get("/api/runs", headers=hc).status_code == 401
    store.write(Put({"pk": "TOKEN", "sk": "alice", "sha256": sha256("fdy_" + "b" * 43)}))  # same name, new token
    clock.t += 61
    assert a.get("/api/runs", headers=hc).status_code == 401  # the old session is bound to the old token's hash


def test_tampered_and_expired_cookies_are_refused(deploy):
    _, clock, a, _ = deploy
    _, cookie, _ = login(a)
    name, expiry, mac = cookie.split(".", 2)
    for bad in (f"bob.{expiry}.{mac}", f"{name}.{int(expiry) + 999}.{mac}", f"{name}.{expiry}.x{mac[1:]}", "junk"):
        assert a.get("/api/runs", headers={"cookie": f"fdy_session={bad}"}).status_code == 401
    clock.t += 12 * 3600 + 1
    assert a.get("/api/runs", headers={"cookie": f"fdy_session={cookie}"}).status_code == 401


def test_sign_in_is_rate_limited_and_checks_origin(deploy):
    _, _, a, _ = deploy
    assert a.post("/api/auth/login", json={"token": TOKEN}, headers={"origin": "https://evil.example"}).status_code == 403
    codes = [login(a, "fdy_wrong")[0].status_code for _ in range(11)]
    assert codes[:9] == [401] * 9 and 429 in codes[9:]  # the evil-origin attempt never reached the limiter


def test_fails_closed_when_the_token_store_is_gone(deploy):
    store, clock, a, _ = deploy
    h = {"x-foundry-token": TOKEN}
    assert a.get("/api/runs", headers=h).status_code == 200

    def broken(*args, **kw):
        raise RuntimeError("DynamoDB unavailable")
    store.query = broken
    clock.t += 61
    assert a.get("/api/runs", headers=h).status_code != 401  # the last good copy, for a while
    clock.t += 601
    r = a.get("/api/runs", headers=h)
    assert r.status_code == 500 and "unavailable" in r.json()["error"]


def test_ipv6_clients_are_limited_by_their_64():
    assert client_key("2001:db8:1:2:3:4:5:6") == client_key("2001:db8:1:2:ffff::1") == "2001:db8:1:2::/64"
    assert client_key("203.0.113.7") == "203.0.113.7"


def test_a_token_name_with_dots_keeps_its_browser_session(deploy):  # Verify pass 2: "jane.doe" looped on sign-in
    store, _, a, _ = deploy
    other = "fdy_" + "b" * 43
    store.write(Put({"pk": "TOKEN", "sk": "jane.doe", "sha256": sha256(other)}))
    r, cookie, _ = login(a, other)
    assert r.status_code == 200 and cookie
    assert a.get("/api/session", headers={"cookie": f"fdy_session={cookie}"}).json()["user"] == "jane.doe"
