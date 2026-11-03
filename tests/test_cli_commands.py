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


def foundry(home: Path, *args: str, timeout: float = 120, **extra) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("FOUNDRY_")}
    return subprocess.run([sys.executable, "-m", "foundry", *args], cwd=home, capture_output=True, text=True,
                          env={**env, "FOUNDRY_HOME": str(home), **extra}, timeout=timeout)


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
    assert r.returncode == 2 and r.stderr.strip() == "Drafting a rubric needs a Bedrock judge model: use config bedrock"
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
    assert "Writing 1 pending trace(s); Ctrl-C again to drop them." in p.stderr.read()
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
        base, deadline = f"http://127.0.0.1:{port}", time.monotonic() + 30
        while True:
            try:
                health = urllib.request.urlopen(f"{base}/healthz", timeout=5)
                break
            except OSError:
                assert time.monotonic() < deadline, "the UI never answered /healthz"
                time.sleep(0.1)
        assert health.status == 200
        body = json.loads(urllib.request.urlopen(f"{base}/api/session", timeout=5).read())
        assert "offline" in body["configs"] and "adb-001" in body["prompts"]
    finally:
        p.send_signal(signal.SIGINT)
        p.wait(15)


# --- tools ----------------------------------------------------------------------------------------------------------


def test_tools_lists_each_servers_tools(home, tmp_path):
    """The in-repo STAC server, as config/offline.toml declares it; listing its tools needs no network."""
    offline = (home / "config" / "offline.toml").read_text()
    start, end = offline.index("[mcp.servers.roda]"), offline.index("[mcp.servers.stac]")
    (home / "config" / "offline.toml").unlink()
    stac_only = offline[:start] + offline[end:]
    e_start = stac_only.index("[mcp.servers.embeddings]")
    e_end = stac_only.index("\n[", e_start) + 1
    (home / "config" / "stac.toml").write_text(stac_only[:e_start] + stac_only[e_end:])
    r = foundry(home, "tools", STAC_API_URL="http://127.0.0.1:9")  # nothing listens there
    assert r.returncode == 0, r.stderr
    listed = json.loads(r.stdout)
    (server,) = listed["servers"]
    assert server["name"] == "stac" and not server["error"]
    assert [t["name"] for t in listed["tools"]] == ["get_item", "list_collections", "search_items"]
    out = tmp_path / "tools.json"
    assert foundry(home, "tools", "--out", str(out), STAC_API_URL="http://127.0.0.1:9").returncode == 0
    assert [s["name"] for s in json.loads(out.read_text())["servers"]] == ["stac"]


def test_tools_with_no_server_listed_exits_1_and_keeps_the_last_file(home, tmp_path):
    for f in (home / "config").glob("*.toml"):
        f.unlink()
    out = tmp_path / "tools.json"
    out.write_text('{"servers": ["the last good list"]}')
    r = foundry(home, "tools", "--out", str(out))
    assert r.returncode == 1 and "kept the previous list" in r.stderr
    assert out.read_text() == '{"servers": ["the last good list"]}'


# --- exit codes the reference documents, each from a fresh process --------------------------------------------------


def test_validate_exit_codes(home, tmp_path):
    r = foundry(home, "validate", str(tmp_path / "nope"))
    assert r.returncode == 2 and r.stderr.startswith("Not a run directory")
    r = foundry(home, "validate", str(ROOT / "fixtures/runs/clean"))
    assert r.returncode == 0 and not r.stdout.strip()
    broken = tmp_path / "broken"
    shutil.copytree(ROOT / "fixtures/runs/clean", broken)
    (broken / "run.json").write_text("{}")
    r = foundry(home, "validate", str(broken))
    assert r.returncode == 1 and r.stdout.startswith("run.json")


def test_receive_with_a_bad_config_exits_2(home, tmp_path):
    r = foundry(home, "receive", "--task", "adb", "--runs-dir", str(tmp_path / "runs"), "--config", "config/nope.toml")
    assert r.returncode == 2 and "nope.toml" in r.stderr


def test_batch_exit_codes(home, tmp_path):
    cfg = write_config(tmp_path / "w", [{"text": "x"}])
    r = foundry(home, "batch", "--config", str(cfg), "--prompts", "nope", "--out", str(tmp_path / "b0"))
    assert r.returncode == 2 and "Unknown prompt id 'nope'" in r.stderr
    cfg.write_text(cfg.read_text().replace(str(tmp_path / "w" / "script.json"), str(tmp_path / "missing.json")))
    r = foundry(home, "batch", "--config", str(cfg), "--prompts", "adb-001", "--out", str(tmp_path / "b1"))
    assert r.returncode == 1 and "[1/1] test adb-001 r1: error" in r.stdout  # its run couldn't start
    assert json.loads((tmp_path / "b1" / "batch.json").read_text())["exit_code"] == 1


def test_evaluate_a_run_already_being_scored_exits_2(home, fixture_run):
    from foundry.data import locks

    run = fixture_run("clean")
    token = locks.acquire(run, owner="another scorer")  # held by this (live) test process
    try:
        r = foundry(home, "evaluate", str(run))
        assert r.returncode == 2 and r.stderr.strip()
        assert not (run / "scores.json").exists() or (run / "scores.json").read_text() == (
            ROOT / "fixtures/runs/clean/scores.json").read_text()
    finally:
        locks.release(run, token)


def test_rubric_draft_exit_0_and_1(home, tmp_path):
    good = tmp_path / "clay-rubric.json"
    good.write_text(json.dumps(GOOD))  # already a rubric: no model call needed, so the offline config works
    r = foundry(home, "rubric", "draft", "--file", str(good), "--config", "config/offline.toml")
    assert r.returncode == 0 and r.stdout.startswith("draft ") and ": valid" in r.stdout, r.stderr
    assert "note: Source was already a valid rubric." in r.stdout and "foundry rubric activate --task adb" in r.stdout

    script = tmp_path / "judge.json"  # a judge that never replies with a rubric: the draft is saved, but invalid
    script.write_text(json.dumps([{"text": "no rubric here"}, {"text": "still none"}]))
    cfg = home / "config" / "junk-judge.toml"
    cfg.write_text((home / "config" / "offline.toml").read_text().replace(
        '[judge]\nprovider = "offline"\nid = "offline-judge"',
        f'[judge]\nprovider = "scripted"\nid = "junk-judge"\nscript = "{script}"'))
    assert 'id = "junk-judge"' in cfg.read_text()
    r = foundry(home, "rubric", "draft", "--text", "Accuracy counts double.", "--config", str(cfg))
    assert r.returncode == 1 and ": INVALID: " in r.stdout, (r.stdout, r.stderr)
    assert "  draft " in foundry(home, "rubric", "list").stdout and " invalid from " in foundry(home, "rubric", "list").stdout


def test_tools_lists_embeddings_tools(home):
    """The in-repo embeddings server, as config/offline.toml declares it; listing its tools needs no network."""
    offline = (home / "config" / "offline.toml").read_text()
    start, end = offline.index("[mcp.servers.roda]"), offline.index("[mcp.servers.embeddings]")
    (home / "config" / "offline.toml").unlink()
    (home / "config" / "emb.toml").write_text(offline[:start] + offline[end:])
    r = foundry(home, "tools")
    assert r.returncode == 0, r.stderr
    listed = json.loads(r.stdout)
    assert [s["name"] for s in listed["servers"]] == ["embeddings"] and not listed["servers"][0]["error"]
    assert [t["name"] for t in listed["tools"]] == ["embedding_change", "embedding_coverage", "similar_places"]
