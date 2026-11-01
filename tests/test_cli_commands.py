"""`foundry` commands not covered elsewhere, each through the real entry point in a fresh process (cli spec C4); and
every argument of both CLIs carrying help text (C1). The project is a copy under FOUNDRY_HOME, so nothing here
touches the repo's own tasks/ or runs/, and the only MCP server is the test fault server (no network)."""

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from conftest import ROOT
from harness_helpers import write_config

from foundry.cli import _build_parser
from foundry.cli_remote import parser as remote_parser
from foundry.data.rubrics import Store
from foundry.engine.rubrics.draft import DraftResult
from foundry.engine.rubrics.sources import Source
from test_cli_remote import TRACE

GOOD = {"rubric_id": "clay-adb", "version": "1.0", "task_id": "adb", "description": "From Clay.",
        "criteria": [{"id": "accuracy", "name": "Accuracy", "description": "Faithful.", "max": 3, "weight": 1,
                      "levels": {"1": "Wrong", "3": "Right"}}]}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def home(tmp_path):
    """A copy of the project (tasks/ and an offline config) for FOUNDRY_HOME."""
    root = tmp_path / "project"
    shutil.copytree(ROOT / "tasks", root / "tasks", ignore=shutil.ignore_patterns("drafts"))
    (root / "config").mkdir()
    shutil.copy(ROOT / "config" / "offline.toml", root / "config" / "offline.toml")
    return root


def foundry(home: Path, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("FOUNDRY_")}
    return subprocess.run([sys.executable, "-m", "foundry", *args], cwd=home, capture_output=True, text=True,
                          env={**env, "FOUNDRY_HOME": str(home)}, timeout=timeout)


def spawn(home: Path, *args: str) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if not k.startswith("FOUNDRY_")}
    return subprocess.Popen([sys.executable, "-u", "-m", "foundry", *args], cwd=home, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env={**env, "FOUNDRY_HOME": str(home)})


# --- C1: help text --------------------------------------------------------------------------------------------------


def _arguments(p: argparse.ArgumentParser, path: str = ""):
    """(command path, action) for every argument of every (sub)command."""
    for action in p._actions:
        if isinstance(action, argparse._SubParsersAction):
            helps = {c.dest: c.help for c in action._choices_actions}
            for name, child in action.choices.items():
                yield f"{path} {name}".strip(), helps.get(name)
                if name != "remote" or path:  # `foundry remote` is cli_remote's parser, walked on its own
                    yield from _arguments(child, f"{path} {name}".strip())
        elif not isinstance(action, argparse._HelpAction) and action.help != argparse.SUPPRESS:
            yield path, action


@pytest.mark.parametrize("name,p", [("foundry", _build_parser()), ("foundry-remote", remote_parser())])
def test_every_command_and_argument_has_help(name, p):
    missing = []
    for path, item in _arguments(p):
        if isinstance(item, argparse.Action):
            if not (item.help or "").strip():
                missing.append(f"{name} {path} {'/'.join(item.option_strings) or item.dest}")
        elif not (item or "").strip():
            missing.append(f"{name} {path} (the command)")
    assert not missing, missing


def _leaves(p: argparse.ArgumentParser, path: str = ""):
    """(command path, parser) for every command that takes arguments itself (not a group of subcommands)."""
    subs = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)]
    if not subs:
        yield path, p
    for action in subs:
        for name, child in action.choices.items():
            yield from _leaves(child, f"{path} {name}".strip())


def _sections() -> dict[str, str]:
    """docs/cli.md split at its "### `<command>`" headings."""
    out, name = {}, None
    for line in (ROOT / "docs" / "cli.md").read_text().splitlines():
        if line.startswith("### `") and line.endswith("`"):
            name = line[5:-1]
            out[name] = ""
        elif line.startswith("## "):
            name = None
        elif name:
            out[name] += line + "\n"
    return out


@pytest.mark.parametrize("prog,p", [("foundry", _build_parser()), ("foundry-remote", remote_parser())])
def test_the_reference_documents_every_command_and_option(prog, p):  # C2: docs/cli.md can't drift from the parsers
    sections, missing = _sections(), []
    for path, leaf in _leaves(p):
        title = f"{prog} {path}"
        if title not in sections:
            missing.append(f"no section for {title}")
            continue
        if title == "foundry remote":
            continue  # cli_remote's own parser, documented under foundry-remote
        for action in leaf._actions:
            if isinstance(action, argparse._HelpAction) or action.help == argparse.SUPPRESS or action.dest == "json":
                continue
            names = [o for o in action.option_strings if o.startswith("--")] or [f"<{action.dest}>"]
            if action.dest == "cmd":
                names = ["<command>"]
            if not any(f"`{n}`" in sections[title] or f"`{n} " in sections[title] for n in names):
                missing.append(f"{title}: {names[0]}")
    assert not missing, missing


def test_bare_foundry_and_the_remote_hand_off(home):
    r = foundry(home)
    assert r.returncode == 2 and "required" in r.stderr
    r = foundry(home, "--help")
    assert r.returncode == 0 and "docs/cli.md" in r.stdout and "rubric" in r.stdout and "remote" in r.stdout
    r = subprocess.run([sys.executable, "-m", "foundry", "remote", "--json", "whoami"], cwd=home, capture_output=True,
                       text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(home), "XDG_CONFIG_HOME": str(home / "c")})
    assert r.returncode == 2 and r.stderr.startswith("foundry remote: Not logged in")


# --- rubric ---------------------------------------------------------------------------------------------------------


def test_rubric_list_activate_rollback_discard(home):
    r = foundry(home, "rubric", "list")
    assert r.returncode == 0 and r.stdout.startswith("* adb-rubric-v0 v0.1 (rubric.json;"), r.stderr
    store = Store(home / "tasks" / "adb")
    src = Source("file", "clay.pdf", "pdf", b"%PDF-1")
    keep = store.save_draft(src, DraftResult(GOOD, model="m", prompt_id="p"))
    drop = store.save_draft(src, DraftResult({**GOOD, "version": "2.0"}, model="m", prompt_id="p"))

    r = foundry(home, "rubric", "list", "--task", "adb")
    assert f"  draft {keep} valid from clay.pdf" in r.stdout and f"  draft {drop} valid" in r.stdout

    r = foundry(home, "rubric", "activate", keep)
    assert r.returncode == 0 and r.stdout.startswith("active: clay-adb v1.0 ("), r.stderr
    assert json.loads((home / "tasks/adb/rubric.json").read_text())["rubric_id"] == "clay-adb"
    lines = foundry(home, "rubric", "list").stdout.splitlines()
    assert any(x.startswith("* ") and "clay-adb" in x for x in lines)
    original = next(x.split()[0] for x in lines if x.startswith("  ") and "adb-rubric-v0" in x)

    r = foundry(home, "rubric", "activate", original)  # a history file name rolls back
    assert r.returncode == 0 and r.stdout.startswith("active: adb-rubric-v0 v0.1"), r.stderr

    r = foundry(home, "rubric", "discard", drop)
    assert r.returncode == 0 and r.stdout.strip() == f"discarded {drop}"
    assert drop not in foundry(home, "rubric", "list").stdout


def test_rubric_errors_exit_2(home):
    r = foundry(home, "rubric", "list", "--task", "nope")
    assert r.returncode == 2 and r.stderr.strip() == "Unknown task 'nope'"
    r = foundry(home, "rubric", "activate", "no-such-draft")
    assert r.returncode == 2 and r.stderr.strip()
    r = foundry(home, "rubric", "discard", "no-such-draft")
    assert r.returncode == 2 and r.stderr.strip()
    r = foundry(home, "rubric", "draft", "--text", "Accuracy counts double.", "--config", "config/offline.toml")
    assert r.returncode == 2 and "draft" in r.stderr.lower()  # the offline judge can't draft: say so, don't invent
    r = foundry(home, "rubric", "draft", "--file", "missing.pdf", "--config", "config/offline.toml")
    assert r.returncode == 2 and "missing.pdf" in r.stderr
    r = foundry(home, "rubric", "draft", "--config", "config/offline.toml")
    assert r.returncode == 2 and "one of the arguments" in r.stderr
    assert not list((home / "tasks/adb/rubrics").glob("drafts/*"))


# --- receive --------------------------------------------------------------------------------------------------------


def _readline_until(p: subprocess.Popen, text: str, seconds: float = 30) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        line = p.stdout.readline()
        if text in line:
            return line
        if not line and p.poll() is not None:
            break
    raise AssertionError(f"no {text!r} from {p.args}: {p.stderr.read() if p.poll() is not None else ''}")


def test_receive_turns_a_posted_trace_into_a_run_and_stops_on_ctrl_c(home, tmp_path):
    port, runs = _free_port(), tmp_path / "runs"
    p = spawn(home, "receive", "--task", "adb", "--prompt-id", "via-receive", "--runs-dir", str(runs), "--port", str(port))
    try:
        line = _readline_until(p, "Receiving OTLP/HTTP")
        assert f"http://127.0.0.1:{port}/v1/traces" in line and f"OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:{port}" in line
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/traces", data=json.dumps(TRACE).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        assert urllib.request.urlopen(req, timeout=10).status == 200
        written = _readline_until(p, "run written:").split("run written:")[1].strip()
        run = json.loads((Path(written) / "run.json").read_text())
        assert run["prompt_id"] == "via-receive" and Path(written).parent == runs
    finally:
        p.send_signal(signal.SIGINT)
        assert p.wait(15) == 0


def test_receive_writes_a_trace_that_arrives_just_before_ctrl_c(home, tmp_path):
    """Regression (cli spec Verify): Ctrl-C inside the 2 s grace period used to drop a trace already answered 200."""
    port, runs = _free_port(), tmp_path / "runs"
    p = spawn(home, "receive", "--task", "adb", "--runs-dir", str(runs), "--port", str(port))
    _readline_until(p, "Receiving OTLP/HTTP")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/traces", data=json.dumps(TRACE).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    assert urllib.request.urlopen(req, timeout=10).status == 200
    p.send_signal(signal.SIGINT)
    assert p.wait(30) == 0
    assert "run written:" in p.stdout.read()
    assert [d.name for d in runs.iterdir()] == ["otel-imported-0af7651916cd-a1"]


def test_receive_on_a_taken_port_exits_2(home, tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        r = foundry(home, "receive", "--task", "adb", "--runs-dir", str(tmp_path / "runs"), "--port", str(port))
    assert r.returncode == 2 and r.stderr.startswith(f"Cannot listen on 127.0.0.1:{port}")


# --- ui -------------------------------------------------------------------------------------------------------------


def test_ui_serves_from_a_fresh_process(home, tmp_path):
    port = _free_port()
    p = spawn(home, "ui", "--port", str(port), "--runs-dir", str(tmp_path / "runs"))
    try:
        assert _readline_until(p, "Foundry UI on").strip() == f"Foundry UI on http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                body = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/session", timeout=5).read())
                break
            except OSError:
                time.sleep(0.1)
        assert "offline" in body["configs"] and "adb-001" in body["prompts"]
    finally:
        p.send_signal(signal.SIGINT)
        p.wait(15)


# --- tools ----------------------------------------------------------------------------------------------------------


def test_tools_lists_each_servers_tools(home, tmp_path):
    (home / "config" / "offline.toml").unlink()
    shutil.copy(write_config(tmp_path / "w", [{"text": "x"}]), home / "config" / "fault.toml")
    r = foundry(home, "tools")
    assert r.returncode == 0, r.stderr
    listed = json.loads(r.stdout)
    (server,) = listed["servers"]
    assert server["name"] == "fault" and not server["error"]
    assert {"echo", "fail", "sleep"} <= {t["name"] for t in listed["tools"] if t["server"] == "fault"}
    out = tmp_path / "tools.json"
    assert foundry(home, "tools", "--out", str(out)).returncode == 0
    assert [s["name"] for s in json.loads(out.read_text())["servers"]] == ["fault"]


def test_tools_with_no_server_listed_exits_1_and_keeps_the_last_file(home, tmp_path):
    for f in (home / "config").glob("*.toml"):
        f.unlink()
    out = tmp_path / "tools.json"
    out.write_text('{"servers": ["the last good list"]}')
    r = foundry(home, "tools", "--out", str(out))
    assert r.returncode == 1 and "kept the previous list" in r.stderr
    assert out.read_text() == '{"servers": ["the last good list"]}'
