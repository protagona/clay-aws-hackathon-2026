"""The remote CLI (aws-deploy spec I3; cli spec C5, C6): the served .pyz, run as its own process against a real
local server. The agent is scripted and its MCP server is the test fault server, so nothing here needs a network."""

import ast
import json
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
import uvicorn
from conftest import ROOT
from harness_helpers import write_config

from foundry.server import create_app
from foundry.ui.spa import CLI_SOURCE, cli_pyz

TRACE = {"resourceSpans": [{"scopeSpans": [{"spans": [
    {"traceId": "0af7651916cd43dd8448eb211c80319c", "spanId": sid, "parentSpanId": parent, "name": name,
     "startTimeUnixNano": str(t * 10**9), "endTimeUnixNano": str(t * 10**9 + 10**8), "status": {}, "events": [],
     "attributes": [{"key": "gen_ai.operation.name", "value": {"stringValue": op}}]}
    for sid, parent, name, t, op in (("a1", "", "invoke_agent cli", 1, "invoke_agent"), ("a2", "a1", "chat", 2, "chat"))]}]}]}

OK_TURNS = [{"tool": "echo", "input": {"text": "x"}}, {"text": "done"}]


def test_the_pyz_is_standard_library_python_3_9_and_reproducible():
    tree = ast.parse(CLI_SOURCE.read_text(), feature_version=(3, 9))
    imported = {(n.module if isinstance(n, ast.ImportFrom) else a.name).split(".")[0]
                for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    assert imported - {"__future__"} <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)
    data = cli_pyz()
    assert data.startswith(b"#!/usr/bin/env python3\n")
    with zipfile.ZipFile(BytesIO(data)) as z:
        assert sorted(z.namelist()) == ["__main__.py", "cli_remote.py"]
        assert {i.date_time for i in z.infolist()} == {(1980, 1, 1, 0, 0, 0)}
    cli_pyz.cache_clear()
    assert cli_pyz() == data


def _config(tmp_path, name: str, turns: list[dict], **kw):
    """A scripted config on the fault server, saved as config/<name>.toml of the served project."""
    src = write_config(tmp_path / f"cfg-{name}", turns, **kw)
    shutil.copy(src, tmp_path / "project" / "config" / f"{name}.toml")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def served(tmp_path):
    """A real `foundry ui` server on a free port, on a copy of the project; and the .pyz it serves.

    Configs: `ok` succeeds, `broken` fails (its tool server crashes on start), `slow` sleeps for 60 s."""
    root = tmp_path / "project"
    (root / "config").mkdir(parents=True)
    (root / "fixtures").symlink_to(ROOT / "fixtures")
    shutil.copytree(ROOT / "tasks", root / "tasks", ignore=shutil.ignore_patterns("drafts"))
    _config(tmp_path, "ok", OK_TURNS)
    _config(tmp_path, "broken", [{"text": "x"}], server_args=["--crash-on-start"])
    _config(tmp_path, "slow", [{"tool": "sleep", "input": {"seconds": 60}}, {"text": "done"}])
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(tmp_path / "runs", root, port=port), host="127.0.0.1", port=port,
                                           log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    import urllib.request

    pyz = tmp_path / "foundry-remote.pyz"
    pyz.write_bytes(urllib.request.urlopen(f"{url}/cli/foundry-remote.pyz").read())  # the download, no token
    yield url, pyz, tmp_path
    server.should_exit = True
    t.join(10)


def _env(home, **extra) -> dict:
    return {"PATH": "/usr/bin:/bin", "HOME": str(home), "XDG_CONFIG_HOME": str(home / "cfg"), **extra}


def cli(pyz, home, *args, python=sys.executable, **env):
    r = subprocess.run([python, str(pyz), *args], capture_output=True, text=True, timeout=300, env=_env(home, **env))
    return r.returncode, r.stdout, r.stderr


def jcli(pyz, home, *args, after: bool = False):
    """The command with --json, before the command or (after=True) after it; the parsed JSON."""
    code, out, err = cli(pyz, home, *((*args, "--json") if after else ("--json", *args)))
    assert code == 0, (args, err)
    return json.loads(out)


def cli_json(pyz, home, *args):
    """A read-only command with --json before it and after it: the same JSON both ways (C5)."""
    return jcli(pyz, home, *args), jcli(pyz, home, *args, after=True)


def until(check, seconds: float = 120):
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.5)


def test_the_pyz_drives_a_real_server(served):
    url, pyz, home = served
    code, _, err = cli(pyz, home, "runs")
    assert code == 2 and "Not logged in" in err
    code, out, _ = cli(pyz, home, "login", url, "fdy_anything")
    assert code == 0 and "Logged in" in out
    saved = home / "cfg" / "foundry" / "remote.json"
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600 and json.loads(saved.read_text())["url"] == url

    code, out, err = cli(pyz, home, "run", "start", "--prompt", "adb-001", "--config", "ok", "--wait")
    assert code == 0, err
    run_id = out.splitlines()[0]
    assert "status   succeeded" in out and "scores   not scored yet" in out
    code, out, err = cli(pyz, home, "evaluate", run_id, "--wait")
    assert code == 0 and "scores   overall" in out, err

    code, out, _ = cli(pyz, home, "--json", "runs")
    assert code == 0 and run_id in {c["run_id"] for c in json.loads(out)["runs"]}
    code, out, _ = cli(pyz, home, "run", "show", run_id)
    assert code == 0 and out.startswith(run_id)
    code, out, _ = cli(pyz, home, "compare", run_id, run_id)
    assert code == 0 and "B − A" in out

    code, out, err = cli(pyz, home, "batch", "start", "--config", "ok", "--prompts", "adb-001", "--wait")
    assert code == 0 and "finished" in out, err
    code, out, _ = cli(pyz, home, "batches")
    assert code == 0 and "finished" in out

    trace = home / "trace.json"
    trace.write_text(json.dumps(TRACE))
    code, out, err = cli(pyz, home, "import", str(trace))
    assert code == 0 and "Imported 1 run" in out, err
    code, out, _ = cli(pyz, home, "--json", "jobs")
    assert code == 0 and {j["kind"] for j in json.loads(out)["jobs"]} >= {"run", "evaluate", "batch"}

    code, _, err = cli(pyz, home, "run", "show", "no-such-run")
    assert code == 1 and "Run not found" in err
    code, _, err = cli(pyz, home, "run", "start", "--prompt", "adb-001", "--config", "nope")
    assert code == 1 and err.strip()


def test_every_command_in_text_and_json(served):
    url, pyz, home = served
    assert cli(pyz, home, "login", url, "fdy_anything")[0] == 0

    d = jcli(pyz, home, "run", "start", "--prompt", "adb-001", "--config", "ok", "--wait", after=True)
    assert d["run"]["status"] == "succeeded" and d["scores"] is None
    run_id = d["run"]["run_id"]

    code, out, _ = cli(pyz, home, "runs", "--limit", "1")
    assert code == 0 and out.startswith("RUN") and len(out.split("\n\n")[0].splitlines()) == 2
    assert out.rstrip().splitlines()[-1].startswith("1 runs · ")  # the KPI line
    a, b = cli_json(pyz, home, "runs")
    assert run_id in {c["run_id"] for c in a["runs"]} and a["kpis"] == b["kpis"]
    a, b = cli_json(pyz, home, "run", "show", run_id)
    assert a == b and a["run"]["run_id"] == run_id

    code, out, _ = cli(pyz, home, "evaluate", run_id)  # no --wait
    assert code == 0 and out.strip() == f"Scoring {run_id}"
    until(lambda: jcli(pyz, home, "run", "show", run_id).get("scores"))
    d = jcli(pyz, home, "evaluate", run_id, "--wait")  # scoring again replaces the scores
    assert d["scores"]["overall"] is not None

    a, b = cli_json(pyz, home, "compare", run_id, run_id)
    assert a == b and a["rows"]
    code, out, _ = cli(pyz, home, "compare", run_id, run_id)
    assert code == 0 and out.startswith(f"A = {run_id}\nB = {run_id}")

    code, out, _ = cli(pyz, home, "batches")
    assert code == 0 and out.strip() == "No batches yet."
    d = jcli(pyz, home, "batch", "start", "--config", "ok", "--prompts", "adb-001,adb-002", "--evaluate", "--wait")
    batch_id = d["batch_id"]
    assert d["meta"]["status"] == "finished" and len(d["rows"]) == 2  # one model × two prompts
    code, out, _ = cli(pyz, home, "batch", "show", batch_id)
    assert code == 0 and out.startswith(f"{batch_id}: finished") and "VARIANT" in out
    a, b = cli_json(pyz, home, "batches")
    assert a == b and batch_id in {x["id"] for x in a["batches"]}
    code, out, _ = cli(pyz, home, "batch", "start", "--config", "ok", "--prompts", "adb-003")  # no --wait
    assert code == 0 and out.startswith("Started batch ")
    second = out.split()[-1]
    code, _, err = cli(pyz, home, "batch", "show", second)  # its record may not exist yet: "Batch not found"
    assert code == 0 or "Batch not found" in err

    def finished():
        code, out, _ = cli(pyz, home, "--json", "batch", "show", second)
        return code == 0 and json.loads(out)["meta"].get("status") == "finished"
    until(finished)

    trace = home / "trace.json"
    trace.write_text(json.dumps(TRACE))
    d = jcli(pyz, home, "import", str(trace), "--prompt-id", "cli-import", after=True)
    assert len(d["runs"]) == 1
    code, out, _ = cli(pyz, home, "jobs")
    assert code == 0 and out.startswith("JOB") and "evaluate" in out
    a, b = cli_json(pyz, home, "jobs")
    assert {"run", "evaluate", "batch"} <= {j["kind"] for j in a["jobs"]}

    code, out, err = cli(pyz, home, "run", "start", "--prompt", "adb-002", "--config", "slow")  # no --wait
    assert code == 0 and out.startswith("Started "), err
    code, _, err = cli(pyz, home, "run", "start", "--prompt", "adb-001", "--config", "ok")
    assert code == 1 and "A run is already in progress" in err  # one run at a time on a laptop's server


def test_login_whoami_logout(served):
    url, pyz, home = served
    code, out, _ = cli(pyz, home, "logout")  # nothing saved yet: still fine
    assert code == 0 and "Forgot the saved login" in out
    code, out, _ = cli(pyz, home, "login", url + "/", "fdy_x")
    assert code == 0 and f"Logged in to {url} as (no sign-in on this server)" in out  # trailing slash dropped
    code, out, _ = cli(pyz, home, "whoami")
    assert code == 0 and out.strip() == f"(no sign-in on this server) at {url}"
    code, out, _ = cli(pyz, home, "whoami", "--json")
    assert code == 0 and json.loads(out) == {"name": "(no sign-in on this server)"}
    saved = home / "cfg" / "foundry" / "remote.json"
    saved.chmod(0o644)
    assert cli(pyz, home, "login", url, "fdy_y")[0] == 0
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600  # a re-login tightens an existing file
    code, out, _ = cli(pyz, home, "--json", "logout")
    assert code == 0 and json.loads(out) == {"ok": True} and not saved.exists()
    code, _, err = cli(pyz, home, "whoami")
    assert code == 2 and "Not logged in" in err


def test_the_environment_overrides_the_saved_login(served):
    url, pyz, home = served
    code, out, err = cli(pyz, home, "whoami", FOUNDRY_URL=url, FOUNDRY_TOKEN="fdy_env")  # nothing saved
    assert code == 0 and out.strip().endswith(f"at {url}"), err
    assert cli(pyz, home, "login", url, "fdy_saved")[0] == 0
    dead = f"http://127.0.0.1:{_free_port()}"
    code, _, err = cli(pyz, home, "runs", FOUNDRY_URL=dead)
    assert code == 1 and f"Can't reach {dead}" in err  # FOUNDRY_URL wins over the saved URL


def test_usage_errors_exit_2(served):
    url, pyz, home = served
    code, _, err = cli(pyz, home)
    assert code == 2 and "required" in err
    code, _, err = cli(pyz, home, "run", "start", "--prompt", "adb-001")
    assert code == 2 and "--config" in err
    code, _, err = cli(pyz, home, "runs", "--limit", "many")
    assert code == 2 and "invalid int value" in err
    code, _, err = cli(pyz, home, "login", "http://example.com", "fdy_x")
    assert code == 2 and "must be https://" in err
    assert not (home / "cfg" / "foundry" / "remote.json").exists()
    assert cli(pyz, home, "login", url, "fdy_x")[0] == 0
    code, _, err = cli(pyz, home, "import", str(home / "missing.json"))
    assert code == 2 and "No such file" in err
    code, out, _ = cli(pyz, home, "--help")
    assert code == 0 and "login" in out and "docs/cli.md" in out


def test_api_and_network_errors_exit_1(served):
    url, pyz, home = served
    dead = f"http://127.0.0.1:{_free_port()}"
    code, _, err = cli(pyz, home, "login", dead, "fdy_x")
    assert code == 1 and f"Can't reach {dead}" in err
    assert not (home / "cfg" / "foundry" / "remote.json").exists()  # a failed login saves nothing
    assert cli(pyz, home, "login", url, "fdy_x")[0] == 0

    code, out, err = cli(pyz, home, "run", "start", "--prompt", "adb-001", "--config", "broken", "--wait")
    assert code == 1 and "status   failed" in out, err  # the run record still prints
    code, _, err = cli(pyz, home, "run", "start", "--prompt", "no-such-prompt", "--config", "ok")
    assert code == 1 and "no-such-prompt" in err
    for args in (("run", "show", "nope"), ("evaluate", "nope"), ("batch", "show", "nope"), ("compare", "nope", "nope")):
        code, out, err = cli(pyz, home, *args)
        assert code == 1 and not out and err.startswith("foundry-remote: "), args
    bad = home / "bad.json"
    bad.write_text("not a trace")
    code, _, err = cli(pyz, home, "import", str(bad))
    assert code == 1 and err.strip()


def test_ctrl_c_while_waiting_exits_130(served):
    url, pyz, home = served
    assert cli(pyz, home, "login", url, "fdy_x")[0] == 0
    p = subprocess.Popen([sys.executable, str(pyz), "run", "start", "--prompt", "adb-001", "--config", "slow", "--wait"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_env(home))
    seen, deadline = "", time.monotonic() + 60
    while "waiting" not in seen:  # the progress line: the job is being waited on
        assert time.monotonic() < deadline and p.poll() is None, seen
        seen += p.stderr.read(1)
    p.send_signal(signal.SIGINT)
    assert p.wait(10) == 130


def _python(version: str) -> str | None:
    """A CPython of that minor version: uv's, else the system python3 (macOS ships 3.9)."""
    uv = shutil.which("uv")
    if uv:
        r = subprocess.run([uv, "python", "find", "--no-project", version], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            found = r.stdout.strip()
            v = subprocess.run([found, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], capture_output=True,
                               text=True).stdout.strip()
            if v == version:
                return found
    for py in ("/usr/bin/python3",):
        v = subprocess.run([py, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"], capture_output=True,
                           text=True) if Path(py).exists() else None
        if v is not None and v.stdout.strip() == version:
            return py
    return None


@pytest.mark.parametrize("version", ["3.9", "3.10"])
def test_the_pyz_on_older_pythons(served, version):  # C6
    py = _python(version)
    if py is None:
        pytest.skip(f"no CPython {version} found (uv python find, or /usr/bin/python3)")
    url, pyz, home = served
    assert cli(pyz, home, "login", url, "fdy_x", python=py)[0] == 0
    code, out, err = cli(pyz, home, "--json", "run", "start", "--prompt", "adb-001", "--config", "ok", "--wait", python=py)
    assert code == 0, err
    run_id = json.loads(out)["run"]["run_id"]
    code, out, err = cli(pyz, home, "evaluate", run_id, "--wait", python=py)
    assert code == 0 and "scores   overall" in out, err
    code, out, err = cli(pyz, home, "batch", "start", "--config", "ok", "--prompts", "adb-001", "--wait", python=py)
    assert code == 0 and "finished" in out, err
    trace = home / "trace.json"
    trace.write_text(json.dumps(TRACE))
    for args in (("runs",), ("runs", "--json"), ("run", "show", run_id), ("compare", run_id, run_id), ("batches",),
                 ("jobs",), ("import", str(trace)), ("whoami",), ("logout",)):
        code, _, err = cli(pyz, home, *args, python=py)
        assert code == 0, (args, err)
    code, _, err = cli(pyz, home, "runs", python=py)
    assert code == 2 and "Not logged in" in err
