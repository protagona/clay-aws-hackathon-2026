"""Checks a live deployment end to end (aws-deploy slice 10). Spends real money on the Bedrock checks (a run, its
scoring and a rubric draft: about $0.30) unless --offline.

    FOUNDRY_TOKEN=fdy_... uv run python scripts/cloud_smoke.py https://<id>.lambda-url.us-west-2.on.aws/ [--offline]

Each check prints PASS or FAIL with its evidence; the exit code is the number of failures.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from foundry.cli_remote import ApiError, Client  # noqa: E402  (standard library only)

TRACE = {"resourceSpans": [{"scopeSpans": [{"spans": [
    {"traceId": "5a0e7651916cd43dd8448eb211c80319", "spanId": sid, "parentSpanId": parent, "name": name,
     "startTimeUnixNano": str(t * 10**9), "endTimeUnixNano": str(t * 10**9 + 10**8), "status": {}, "events": [],
     "attributes": [{"key": "gen_ai.operation.name", "value": {"stringValue": op}}]}
    for sid, parent, name, t, op in (("b1", "", "invoke_agent smoke", 1, "invoke_agent"), ("b2", "b1", "chat", 2, "chat"))]}]}]}
RUBRIC_TEXT = ("Score an earth-observation dataset plan on three things, 1 to 5 each: whether it names real, openly "
               "available imagery for the place and dates asked; whether the resolution and revisit fit the task; "
               "and whether it states the steps to get the data, with any access limits.")

failures: list[str] = []


def check(name: str, ok: bool, evidence: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f': {evidence}' if evidence else ''}", flush=True)
    if not ok:
        failures.append(name)
    return ok


def raw(url: str, method: str = "GET", body: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=330) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def wait_job(c: Client, kind: str, run_id: str, limit_s: float) -> dict | None:
    end = time.monotonic() + limit_s
    while time.monotonic() < end:
        jobs = [j for j in c.get("/api/jobs")["jobs"] if j["kind"] == kind and j["run_id"] == run_id]
        if jobs and not jobs[-1]["alive"]:
            return jobs[-1]
        time.sleep(2)
    return None


def public_surface(base: str) -> None:
    status, _, body = raw(base + "/api/runs")
    check("an /api read without a token is 401", status == 401 and json.loads(body) == {"error": "Sign in required"},
          f"{status} {body[:60]!r}")
    status, _, body = raw(base + "/healthz")
    check("/healthz is public and states the build", status == 200 and body.startswith(b"ok "), body.decode()[:40])
    status, headers, body = raw(base + "/")
    check("the web app shell is public", status == 200 and b"<div id=\"root\">" in body, str(status))
    check("its CSP lets the page upload to the state bucket",
          ".s3.us-west-2.amazonaws.com" in {k.lower(): v for k, v in headers.items()}.get("content-security-policy", ""), "")
    status, _, body = raw(base + "/api/runs", headers={"X-Foundry-Token": "fdy_wrong"})
    check("a wrong header token is 401", status == 401, str(status))


def browser_session(base: str, token: str) -> None:
    host = base.split("//", 1)[1]
    status, headers, body = raw(base + "/api/auth/login", "POST", json.dumps({"token": token}).encode(),
                                {"Content-Type": "application/json", "Origin": base})
    cookie = (headers.get("Set-Cookie") or "").split(";")[0]
    flags = (headers.get("Set-Cookie") or "").lower()
    check("sign-in sets a Secure, HttpOnly, SameSite=Strict session cookie",
          status == 200 and cookie.startswith("fdy_session=") and all(f in flags for f in ("secure", "httponly", "samesite=strict")),
          f"{status} {json.loads(body).get('name')}")
    status, _, body = raw(base + "/api/session", headers={"Cookie": cookie})
    session = json.loads(body)
    check("the session cookie reads the API, as its user", status == 200 and session.get("user") and session["receiver"] is False,
          f"user {session.get('user')}, receiver {session.get('receiver')}")
    write = {"Cookie": cookie, "Content-Type": "application/json", "Origin": base}
    status, _, _ = raw(base + "/api/runs", "POST", b'{"prompt_id": "adb-001", "config": "nope"}', write)
    check("a cookie write without the CSRF token is 403", status == 403, str(status))
    status, _, body = raw(base + "/api/runs", "POST", b'{"prompt_id": "adb-001", "config": "nope"}',
                          {**write, "X-CSRF-Token": session["csrf"]})
    check("with it, the write reaches the app", status not in (401, 403), f"{status} {body[:80]!r}")
    status, _, _ = raw(base + "/api/runs", "POST", b'{"prompt_id": "adb-001", "config": "nope"}',
                       {**write, "X-CSRF-Token": session["csrf"], "Origin": "https://evil.example"})
    check("a foreign Origin is refused", status == 403, str(status))
    del host


def tools(c: Client) -> None:
    c.post("/api/prompts/tools/refresh") if not c.get("/api/prompts/tools")["tools"] else None
    end = time.monotonic() + 300
    menu = c.get("/api/prompts/tools")
    while menu["refreshing"] and time.monotonic() < end:
        time.sleep(3)
        menu = c.get("/api/prompts/tools")
    check("the tool menu lists 13 tools with no server errors (RODA built in)",
          len(menu["tools"]) == 13 and not menu["errors"], f"{len(menu['tools'])} tools, errors {menu['errors']}")


def live_run(c: Client) -> str | None:
    """L1: a live Bedrock run's steps show within 10 s of finishing; then score it."""
    run_id = c.post("/api/runs", {"prompt_id": "adb-001", "config": "bedrock"})["run_id"]
    seen: dict[int, float] = {}
    lags: list[float] = []
    started = time.monotonic()
    while time.monotonic() - started < 600:
        try:
            d = c.get(f"/api/runs/{run_id}")
        except ApiError as e:
            if e.status != 404:
                raise
            time.sleep(1)
            continue
        now = time.time()
        for item in d["m"]["feed"]:
            s = item["s"]
            if s["step"] not in seen and s.get("started_at"):
                seen[s["step"]] = now
                done = datetime.fromisoformat(s["started_at"].replace("Z", "+00:00")).timestamp() + s["duration_ms"] / 1000
                if d["run"]["status"] == "running":
                    lags.append(now - done)
        if d["run"]["status"] != "running" and not d["m"]["live"]:
            break
        time.sleep(1)
    status = d["run"]["status"]
    check("a live Bedrock run succeeds in the cloud", status == "succeeded",
          f"{run_id}: {status}, {len(d['m']['feed'])} steps, ${d['m'].get('cost') or 0:.3f}, {d['m']['tools_ok']} tool calls ok, {d['m']['tools_err']} failed")
    check("L1: steps of a running run show within 10 s", bool(lags) and max(lags) < 10,
          f"{len(lags)} steps seen while running, worst lag {max(lags):.1f} s" if lags else "no step seen while running")
    if status != "succeeded":
        return None
    c.post(f"/api/runs/{run_id}/evaluate")
    job = wait_job(c, "evaluate", run_id, 600)
    scores = c.get(f"/api/runs/{run_id}").get("scores")
    check("scoring it in the cloud", job is not None and not job["failed"] and scores is not None,
          f"overall {scores.get('overall') if scores else None}")
    return run_id


def batch(c: Client) -> None:
    batch_id = c.post("/api/batches", {"config": "offline", "prompts": ["adb-001"], "repeats": 2, "concurrency": 2,
                                       "evaluate": True, "max_cost": 1})["batch_id"]
    end, d = time.monotonic() + 900, None
    while time.monotonic() < end:
        try:
            d = c.get(f"/api/batches/{batch_id}")
        except ApiError as e:
            if e.status != 404:
                raise
            time.sleep(3)
            continue
        if not d["running"] and (d["job"] is None or not d["job"]["alive"]):
            break
        time.sleep(3)
    ok = d is not None and d["meta"].get("status") == "finished" and d["done"] == 2
    check("a 2-item batch on Step Functions", ok,
          f"{batch_id}: {d and d['meta'].get('status')}, {d and d['done']}/2, rows {[r['status'] for r in (d or {}).get('rows', [])]}")


def imports(c: Client) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "smoke-trace.json"
        f.write_text(json.dumps(TRACE))
        upload_id = c.upload("trace", f)
    r = c.post("/api/import/by-upload", {"upload_id": upload_id})
    check("a trace imported through a presigned bucket upload", len(r["runs"]) == 1 and bool(c.get(f"/api/runs/{r['runs'][0]}")),
          r["runs"][0])


def draft(c: Client) -> None:
    t0 = time.monotonic()
    try:
        r = c.post("/api/rubric/drafts/by-upload", {"kind": "text", "config": "bedrock", "text": RUBRIC_TEXT})
    except ApiError as e:
        check("a real rubric draft through the URL", False, str(e))
        return
    took = time.monotonic() - t0
    view = c.get(f"/api/rubric/drafts/{r['draft_id']}")
    check("a real rubric draft through the URL", bool(view["body"].get("criteria")),
          f"{len(view['body'].get('criteria') or [])} criteria in {took:.0f} s")
    c.call("DELETE", f"/api/rubric/drafts/{r['draft_id']}?task=adb")


def conflicts(c: Client) -> None:
    base = c.get("/api/prompts")["base_version"]
    pid = c.get("/api/prompts")["next_id"]
    first = c.post("/api/prompts", {"task": "adb", "prompt_id": pid, "prompt": "Smoke test: first edit.", "base_version": base})
    try:
        c.post("/api/prompts", {"task": "adb", "prompt_id": f"{pid}x", "prompt": "Smoke test: second edit.", "base_version": base})
        second = None
    except ApiError as e:
        second = e
    check("two edits from the same version: the second gets 409",
          second is not None and second.status == 409 and str(second) == "Changed by someone else; reload", str(second))
    fresh = c.get("/api/prompts")["base_version"]
    c.post(f"/api/prompts/{first['prompt_id']}/archive", {"task": "adb", "archived": True, "base_version": fresh})


def cli_from_curl(base: str, token: str) -> None:
    import subprocess

    with tempfile.TemporaryDirectory() as tmp:
        pyz = Path(tmp) / "foundry-remote.pyz"
        status, _, body = raw(base + "/cli/foundry-remote.pyz")
        pyz.write_bytes(body)
        env = {"PATH": "/usr/bin:/bin", "HOME": tmp, "XDG_CONFIG_HOME": tmp}
        run = lambda *a: subprocess.run([sys.executable, str(pyz), *a], capture_output=True, text=True, env=env, timeout=120)  # noqa: E731
        login = run("login", base, token)
        runs = run("--json", "runs")
        jobs = run("jobs")
        check("the CLI downloaded with curl, logged in with a token alone",
              status == 200 and login.returncode == 0 and runs.returncode == 0 and jobs.returncode == 0,
              f"{login.stdout.strip()[:60]} · {len(json.loads(runs.stdout)['runs']) if runs.returncode == 0 else runs.stderr} runs")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--offline", action="store_true", help="skip the checks that call Bedrock")
    p.add_argument("--only", default="", help="comma-separated check groups")
    a = p.parse_args()
    base, token = a.url.rstrip("/"), os.environ.get("FOUNDRY_TOKEN", "")
    if not token:
        raise SystemExit("set FOUNDRY_TOKEN")
    c = Client(base, token)
    groups = {"public": lambda: public_surface(base), "session": lambda: browser_session(base, token),
              "tools": lambda: tools(c), "import": lambda: imports(c), "conflicts": lambda: conflicts(c),
              "batch": lambda: batch(c), "cli": lambda: cli_from_curl(base, token),
              "run": lambda: None if a.offline else live_run(c), "draft": lambda: None if a.offline else draft(c)}
    for name in (a.only.split(",") if a.only else groups):
        try:
            groups[name]()
        except Exception as e:  # noqa: BLE001 (a check that crashes is a failed check)
            check(f"{name} (crashed)", False, f"{type(e).__name__}: {e}")
    print(f"\n{len(failures)} failed" + (f": {', '.join(failures)}" if failures else ""))
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(main())
