"""The remote CLI (aws-deploy spec I3, slice 8): the served .pyz, run as a subprocess against a real local server."""

import ast
import json
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import zipfile
from io import BytesIO

import pytest
import uvicorn
from conftest import ROOT

from foundry.server import create_app
from foundry.ui.spa import CLI_SOURCE, cli_pyz

TRACE = {"resourceSpans": [{"scopeSpans": [{"spans": [
    {"traceId": "0af7651916cd43dd8448eb211c80319c", "spanId": sid, "parentSpanId": parent, "name": name,
     "startTimeUnixNano": str(t * 10**9), "endTimeUnixNano": str(t * 10**9 + 10**8), "status": {}, "events": [],
     "attributes": [{"key": "gen_ai.operation.name", "value": {"stringValue": op}}]}
    for sid, parent, name, t, op in (("a1", "", "invoke_agent cli", 1, "invoke_agent"), ("a2", "a1", "chat", 2, "chat"))]}]}]}


def test_the_pyz_is_standard_library_python_3_10_and_reproducible():
    tree = ast.parse(CLI_SOURCE.read_text(), feature_version=(3, 10))
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


@pytest.fixture
def served(tmp_path):
    """A real `foundry ui` server on a free port, on a copy of the project; and the .pyz it serves."""
    root = tmp_path / "project"
    root.mkdir()
    for d in ("config", "fixtures"):
        (root / d).symlink_to(ROOT / d)
    shutil.copytree(ROOT / "tasks", root / "tasks", ignore=shutil.ignore_patterns("drafts"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
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


def cli(pyz, home, *args):
    r = subprocess.run([sys.executable, str(pyz), *args], capture_output=True, text=True, timeout=300,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(home), "XDG_CONFIG_HOME": str(home / "cfg")})
    return r.returncode, r.stdout, r.stderr


def test_the_pyz_drives_a_real_server(served):
    url, pyz, home = served
    code, _, err = cli(pyz, home, "runs")
    assert code == 2 and "Not logged in" in err
    code, out, _ = cli(pyz, home, "login", url, "fdy_anything")
    assert code == 0 and "Logged in" in out
    saved = home / "cfg" / "foundry" / "remote.json"
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600 and json.loads(saved.read_text())["url"] == url

    code, out, err = cli(pyz, home, "run", "start", "--prompt", "adb-001", "--config", "offline", "--wait")
    assert code == 0, err
    run_id = out.splitlines()[0]
    assert "status   succeeded" in out
    code, out, err = cli(pyz, home, "evaluate", run_id, "--wait")
    assert code == 0 and "overall" in out, err

    code, out, _ = cli(pyz, home, "--json", "runs")
    assert code == 0 and run_id in {c["run_id"] for c in json.loads(out)["runs"]}
    code, out, _ = cli(pyz, home, "run", "show", run_id)
    assert code == 0 and out.startswith(run_id)
    code, out, _ = cli(pyz, home, "compare", run_id, run_id)
    assert code == 0 and "B − A" in out

    code, out, err = cli(pyz, home, "batch", "start", "--config", "offline", "--prompts", "adb-001", "--wait")
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
