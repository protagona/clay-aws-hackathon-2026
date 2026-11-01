"""foundry-remote: the Foundry API from a terminal, for a deployment or a laptop's `foundry ui` (aws-deploy spec I3).

Standard library only and Python 3.10+, so a deployment can serve it as one file (`GET /cli/foundry-remote.pyz`) to
people with no clone and no AWS account. A token is all it needs:

    python3 foundry-remote.pyz login https://<id>.lambda-url.us-west-2.on.aws/ fdy_...
    python3 foundry-remote.pyz run start --prompt adb-001 --config bedrock --wait

`login` saves the URL and token to ~/.config/foundry/remote.json (mode 0600); FOUNDRY_URL and FOUNDRY_TOKEN
override it. Text output by default, `--json` for the API's own JSON. Exit codes: 0 success; 1 an API or network
error (the message goes to stderr), or a waited-for run, scoring or batch that didn't succeed; 2 a usage error or no
login.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

TIMEOUT_S = 330  # the API function's own limit is 300 s (a synchronous rubric draft or import)
POLL_S = 2.0


class ApiError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class UsageError(Exception):
    pass


# --- login state ------------------------------------------------------------------------------------------------


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "foundry" / "remote.json"


def save_login(url: str, token: str) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"url": url, "token": token}, f)
    os.chmod(path, 0o600)  # an existing file keeps its old mode through O_CREAT
    return path


def load_login() -> tuple[str, str]:
    saved: dict = {}
    try:
        saved = json.loads(config_path().read_text())
    except (OSError, ValueError):
        pass
    url = os.environ.get("FOUNDRY_URL") or saved.get("url") or ""
    token = os.environ.get("FOUNDRY_TOKEN") or saved.get("token") or ""
    if not url:
        raise UsageError("Not logged in: run `login <url> <token>` (or set FOUNDRY_URL and FOUNDRY_TOKEN).")
    return url, token


# --- HTTP -------------------------------------------------------------------------------------------------------


class Client:
    """JSON over HTTPS with the X-Foundry-Token header. Writes also carry the CSRF token from /api/session: a
    deployment ignores it for header-token callers, a laptop's server (no tokens) requires it."""

    def __init__(self, url: str, token: str):
        self.base = url.rstrip("/")
        self.token = token
        self._csrf: str | None = None

    def _send(self, method: str, url: str, body: bytes | None, headers: dict) -> tuple[int, bytes]:
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(f"Can't reach {self.base}: {getattr(e, 'reason', e)}") from None

    def call(self, method: str, path: str, body: dict | None = None):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["X-Foundry-Token"] = self.token
        data = None
        if method != "GET":
            headers["X-CSRF-Token"] = self.csrf()
            if body is not None:
                headers["Content-Type"] = "application/json"
                data = json.dumps(body).encode()
        status, raw = self._send(method, self.base + path, data, headers)
        try:
            out = json.loads(raw or b"null")
        except ValueError:
            out = None
        if status >= 400:
            message = out.get("error") if isinstance(out, dict) else None
            raise ApiError(message or f"HTTP {status}", status)
        return out

    def csrf(self) -> str:
        if self._csrf is None:
            self._csrf = self.call("GET", "/api/session").get("csrf") or ""
        return self._csrf

    def get(self, path: str):
        return self.call("GET", path)

    def post(self, path: str, body: dict | None = None):
        return self.call("POST", path, {} if body is None else body)

    def upload(self, kind: str, path: Path) -> str:
        """The two-step upload (I6): a ticket, then the file to where it points. Returns the upload id."""
        data = path.read_bytes()
        ticket = self.post("/api/uploads", {"kind": kind, "filename": path.name, "size": len(data)})
        local = ticket["url"].startswith("/")
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                 for k, v in ticket["fields"].items()]
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
                     f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        if local:  # this server's own upload route; a presigned bucket POST takes no credentials of ours
            headers.update({"X-CSRF-Token": self.csrf(), **({"X-Foundry-Token": self.token} if self.token else {})})
        status, raw = self._send("POST", self.base + ticket["url"] if local else ticket["url"], body, headers)
        if status >= 400:
            text = raw.decode(errors="replace")
            if "EntityTooLarge" in text:
                raise ApiError("The file is over the limit.")
            try:
                raise ApiError(json.loads(text).get("error") or f"Upload failed (HTTP {status})")
            except ValueError:
                raise ApiError(f"Upload failed (HTTP {status})") from None
        return ticket["upload_id"]


# --- output -----------------------------------------------------------------------------------------------------


def num(v, digits: int = 2) -> str:
    return "—" if v is None or v == "" else f"{float(v):.{digits}f}"


def money(v) -> str:
    return "—" if v is None else f"${float(v):.3f}"


def table(rows: list[list[str]], header: list[str]) -> str:
    rows = [header, *rows]
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(header))]
    return "\n".join("  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows)


def short_model(m: str | None) -> str:
    return (m or "?").split("/")[-1].split(":")[0]


# --- waiting ----------------------------------------------------------------------------------------------------


def wait_for_job(c: Client, kind: str, run_id: str, quiet: bool) -> dict | None:
    """Poll /api/jobs until the latest `kind` job for `run_id` ends; the job, or None if it never showed up."""
    seen, started = None, time.monotonic()
    while True:
        jobs = [j for j in c.get("/api/jobs")["jobs"] if j["kind"] == kind and j["run_id"] == run_id]
        job = jobs[-1] if jobs else None
        if job is None and seen is None and time.monotonic() - started > 30:
            return None
        if job is not None:
            seen = job
            if not job["alive"]:
                return job
        if not quiet:
            print(f"\r{kind} {run_id}: waiting… {int(time.monotonic() - started)} s", end="", file=sys.stderr, flush=True)
        time.sleep(POLL_S)


def end_line(quiet: bool) -> None:
    if not quiet:
        print(file=sys.stderr)


# --- commands ---------------------------------------------------------------------------------------------------


def cmd_login(c: Client | None, a) -> int:
    url = a.url.rstrip("/")
    if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise UsageError("The URL must be https:// (or http://127.0.0.1 / http://localhost for a laptop).")
    me = whoami(Client(url, a.token))
    path = save_login(url, a.token)
    return out(a, me, f"Logged in to {url} as {me.get('name')} (saved to {path}).")


def cmd_logout(c, a) -> int:
    path = config_path()
    path.unlink(missing_ok=True)
    return out(a, {"ok": True}, f"Forgot the saved login ({path}).")


def whoami(c: Client) -> dict:
    """The token's name; a laptop's server has no sign-in (no /api/auth routes), so any token works there."""
    try:
        return c.get("/api/auth/me")
    except ApiError as e:
        if e.status != 404:
            raise
        c.get("/api/session")  # it is a Foundry server
        return {"name": "(no sign-in on this server)"}


def cmd_whoami(c: Client, a) -> int:
    me = whoami(c)
    return out(a, me, f"{me['name']} at {c.base}")


def cmd_runs(c: Client, a) -> int:
    d = c.get("/api/runs")
    cards = d["runs"][: a.limit] if a.limit else d["runs"]
    rows = [[x["run_id"], x["prompt_id"], short_model(x["model"]),
             "being scored" if x["column"] == "evaluating" else x["status"], num(x.get("overall")),
             money(x.get("cost"))] for x in cards]
    k = d["kpis"]
    text = table(rows, ["RUN", "PROMPT", "MODEL", "STATUS", "OVERALL", "COST"]) + \
        f"\n\n{k['runs']} runs · {k['scored']} scored · mean {num(k['mean'])} · spent {money(k['spent'])} · {k['live']} live"
    return out(a, d, text)


def run_text(d: dict) -> str:
    run, s, m = d["run"], d.get("scores"), d["m"]
    status = "being scored" if d.get("evaluating") else run["status"]
    lines = [f"{run['run_id']}", f"  prompt   {run['prompt_id']}: {(run.get('prompt') or '')[:100]}",
             f"  model    {run['model'].get('id')}", f"  status   {status} · {m.get('elapsed')} · {money(m.get('cost'))}",
             f"  steps    {len(m.get('feed') or [])} ({m.get('tools_ok', 0)} tool calls ok, {m.get('tools_err', 0)} failed)"]
    if s:
        lines.append(f"  scores   overall {num(s.get('overall'))} · workflow {num(s['workflow'].get('score'))} · "
                     f"output {num(s['output'].get('score'))}")
    elif d.get("interrupted"):
        lines.append("  scores   scoring was interrupted; score it again with `evaluate`")
    else:
        lines.append("  scores   not scored yet")
    return "\n".join(lines)


def cmd_run_show(c: Client, a) -> int:
    d = c.get(f"/api/runs/{urllib.parse.quote(a.run_id, safe='')}")
    return out(a, d, run_text(d))


def cmd_run_start(c: Client, a) -> int:
    run_id = c.post("/api/runs", {"prompt_id": a.prompt, "config": a.config})["run_id"]
    if not a.wait:
        return out(a, {"run_id": run_id}, f"Started {run_id}")
    job = wait_for_job(c, "run", run_id, a.json)
    end_line(a.json)
    try:
        d = c.get(f"/api/runs/{urllib.parse.quote(run_id, safe='')}")
    except ApiError:
        message = (job or {}).get("message") or "the run left no record"
        raise ApiError(f"Run {run_id} failed: {message}") from None
    out(a, d, run_text(d))
    return 0 if d["run"]["status"] == "succeeded" else 1


def cmd_evaluate(c: Client, a) -> int:
    c.post(f"/api/runs/{urllib.parse.quote(a.run_id, safe='')}/evaluate")
    if not a.wait:
        return out(a, {"ok": True}, f"Scoring {a.run_id}")
    job = wait_for_job(c, "evaluate", a.run_id, a.json)
    end_line(a.json)
    d = c.get(f"/api/runs/{urllib.parse.quote(a.run_id, safe='')}")
    out(a, d, run_text(d))
    return 0 if job is not None and not job["failed"] and d.get("scores") else 1


def batch_text(d: dict) -> str:
    meta = d["meta"]
    cap = f" of {money(meta['max_cost'])}" if meta.get("max_cost") else ""
    head = (f"{d['batch_id']}: {meta.get('status') or ('running' if d['running'] else '?')} · "
            f"{d['done']}/{meta.get('jobs') or '?'} done · spent {money(meta.get('spent_usd'))}{cap}")
    rows = [[r["variant"], r["prompt"], r["repeat"], r["status"], num(r.get("overall_f")), money(r.get("cost"))]
            for r in d["rows"]]
    return head + ("\n\n" + table(rows, ["VARIANT", "PROMPT", "REPEAT", "STATUS", "OVERALL", "COST"]) if rows else "")


def cmd_batch_start(c: Client, a) -> int:
    body = {"config": a.config, "prompts": split(a.prompts), "models": split(a.models), "repeats": a.repeats,
            "concurrency": a.concurrency, "evaluate": a.evaluate, "max_cost": a.max_cost}
    batch_id = c.post("/api/batches", body)["batch_id"]
    if not a.wait:
        return out(a, {"batch_id": batch_id}, f"Started batch {batch_id}")
    started = time.monotonic()
    while True:
        try:
            d = c.get(f"/api/batches/{urllib.parse.quote(batch_id, safe='')}")
        except ApiError as e:  # its record appears once the batch job has started
            job = next((j for j in reversed(c.get("/api/jobs")["jobs"]) if j["kind"] == "batch" and j["run_id"] == batch_id), None)
            if e.status != 404 or (job is not None and not job["alive"]) or time.monotonic() - started > 60:
                raise
            time.sleep(POLL_S)
            continue
        if not d["running"] and (d["job"] is None or not d["job"]["alive"]):
            break
        if not a.json:
            print(f"\rbatch {batch_id}: {d['done']}/{d['meta'].get('jobs') or '?'} done · "
                  f"{int(time.monotonic() - started)} s", end="", file=sys.stderr, flush=True)
        time.sleep(POLL_S)
    end_line(a.json)
    out(a, d, batch_text(d))
    return 0 if d["meta"].get("status") == "finished" and not d["meta"].get("exit_code") else 1


def cmd_batch_show(c: Client, a) -> int:
    d = c.get(f"/api/batches/{urllib.parse.quote(a.batch_id, safe='')}")
    return out(a, d, batch_text(d))


def cmd_batches(c: Client, a) -> int:
    d = c.get("/api/batches")
    rows = [[b["id"], b["status"], f"{b['done']}/{b['jobs']}", money(b["spent"]), ", ".join(map(short_model, b["models"]))]
            for b in d["batches"]]
    return out(a, d, table(rows, ["BATCH", "STATUS", "DONE", "SPENT", "MODELS"]) if rows else "No batches yet.")


def cmd_compare(c: Client, a) -> int:
    q = urllib.parse.urlencode({"a": a.a, "b": a.b})
    d = c.get(f"/api/compare?{q}")
    rows = [[("  " if r["indent"] else "") + r["label"], r["a"], r["b"], r["delta"]] for r in d["rows"]]
    return out(a, d, f"A = {a.a}\nB = {a.b}\n\n" + table(rows, ["", "A", "B", "B − A"]))


def cmd_import(c: Client, a) -> int:
    path = Path(a.file)
    if not path.is_file():
        raise UsageError(f"No such file: {path}")
    upload_id = c.upload("trace", path)
    d = c.post("/api/import/by-upload", {"upload_id": upload_id, "task": a.task, "prompt_id": a.prompt_id,
                                         "config": a.config})
    return out(a, d, f"Imported {len(d['runs'])} run{'' if len(d['runs']) == 1 else 's'}:\n" + "\n".join(f"  {r}" for r in d["runs"]))


def cmd_jobs(c: Client, a) -> int:
    d = c.get("/api/jobs")
    rows = [[j["id"], j["kind"], j["run_id"], "running" if j["alive"] else "failed" if j["failed"] else "done",
             (j.get("message") or "")[:60]] for j in d["jobs"]]
    return out(a, d, table(rows, ["JOB", "KIND", "RUN", "STATUS", "MESSAGE"]) if rows else "No jobs.")


def split(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def out(a, data, text: str) -> int:
    print(json.dumps(data, indent=2) if a.json else text)
    return 0


# --- parser -----------------------------------------------------------------------------------------------------


def parser(prog: str = "foundry-remote") -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, description="The Foundry eval sandbox API from a terminal.")
    p.add_argument("--json", action="store_true", help="print the API's JSON instead of text")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("login", help="save a deployment's URL and your access token")
    s.add_argument("url")
    s.add_argument("token")
    s.set_defaults(fn=cmd_login, needs_login=False)
    sub.add_parser("logout", help="forget the saved login").set_defaults(fn=cmd_logout, needs_login=False)
    sub.add_parser("whoami", help="who the saved token belongs to").set_defaults(fn=cmd_whoami)

    s = sub.add_parser("runs", help="list runs, newest first")
    s.add_argument("--limit", type=int, default=0)
    s.set_defaults(fn=cmd_runs)

    run = sub.add_parser("run", help="show or start a run").add_subparsers(dest="run_command", required=True)
    s = run.add_parser("show", help="one run's status, cost and scores")
    s.add_argument("run_id")
    s.set_defaults(fn=cmd_run_show)
    s = run.add_parser("start", help="start a run of one prompt")
    s.add_argument("--prompt", required=True, help="prompt id, like adb-001")
    s.add_argument("--config", required=True, help="config name, like bedrock")
    s.add_argument("--wait", action="store_true", help="wait for it to finish (exit 1 unless it succeeds)")
    s.set_defaults(fn=cmd_run_start)

    s = sub.add_parser("evaluate", help="score a run")
    s.add_argument("run_id")
    s.add_argument("--wait", action="store_true", help="wait for the scores (exit 1 if scoring fails)")
    s.set_defaults(fn=cmd_evaluate)

    batch = sub.add_parser("batch", help="start or show a batch").add_subparsers(dest="batch_command", required=True)
    s = batch.add_parser("start", help="run prompts × models × repeats")
    s.add_argument("--config", required=True)
    s.add_argument("--prompts", required=True, help="comma-separated prompt ids")
    s.add_argument("--models", default="", help="comma-separated model ids (default: the config's)")
    s.add_argument("--repeats", type=int, default=1)
    s.add_argument("--concurrency", type=int, default=2)
    s.add_argument("--evaluate", action="store_true", help="score each run")
    s.add_argument("--max-cost", type=float, default=2.0, help="stop starting jobs past this many USD (default 2)")
    s.add_argument("--wait", action="store_true", help="wait for it to finish (exit 1 unless it completes)")
    s.set_defaults(fn=cmd_batch_start)
    s = batch.add_parser("show", help="one batch's progress and rows")
    s.add_argument("batch_id")
    s.set_defaults(fn=cmd_batch_show)
    sub.add_parser("batches", help="list batches").set_defaults(fn=cmd_batches)

    s = sub.add_parser("compare", help="two runs side by side")
    s.add_argument("a")
    s.add_argument("b")
    s.set_defaults(fn=cmd_compare)

    s = sub.add_parser("import", help="import an OTLP/JSON trace file; each agent run in it becomes a run")
    s.add_argument("file")
    s.add_argument("--task", default="adb")
    s.add_argument("--prompt-id", default="imported")
    s.add_argument("--config", default="", help="a config whose prices work out the cost (default: none)")
    s.set_defaults(fn=cmd_import)

    sub.add_parser("jobs", help="list jobs").set_defaults(fn=cmd_jobs)
    return p


def main(argv: list[str] | None = None, prog: str = "foundry-remote") -> int:
    a = parser(prog).parse_args(argv)
    try:
        c = Client(*load_login()) if getattr(a, "needs_login", True) else None
        return a.fn(c, a)
    except UsageError as e:
        print(f"{prog}: {e}", file=sys.stderr)
        return 2
    except ApiError as e:
        print(f"{prog}: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
