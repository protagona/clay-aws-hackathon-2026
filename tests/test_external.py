"""`foundry external` (increment-4 X1-X3): any OTel-instrumented agent command, recorded and evaluated."""

import json
import sys
import time
from pathlib import Path

from conftest import ROOT, foundry

HELPER = str(ROOT / "tests" / "strands_otel_agent.py")


def _ext(tmp_path, *cmd, extra=()):
    return foundry("external", "--task", "adb", "--prompt", "adb-001", "--runs-dir", str(tmp_path / "runs"), *extra,
                   "--", *cmd, timeout=180)


def test_real_strands_agent_recorded_and_evaluated(tmp_path):  # X1, X2, X4
    proc = _ext(tmp_path, sys.executable, HELPER, "env", extra=("--evaluate",))
    assert proc.returncode == 0, proc.stderr[-2000:]
    run_dir, report, scores = proc.stdout.strip().splitlines()  # stdout carries only results
    run = json.loads((Path(run_dir) / "run.json").read_text())
    prompts = {json.loads(ln)["prompt_id"]: json.loads(ln)["prompt"]
               for ln in (ROOT / "tasks/adb/prompts.jsonl").read_text().splitlines()}
    assert run["prompt_id"] == "adb-001" and run["prompt"] == prompts["adb-001"]  # FOUNDRY_PROMPT reached the agent
    assert run["source"]["file"] == "receiver" and report.endswith("report.md") and scores.startswith("overall ")
    assert "Tool 'fail' failed" in proc.stderr  # the agent's own output goes to stderr
    trace = [json.loads(ln) for ln in (Path(run_dir) / "trace.jsonl").read_text().splitlines()]
    assert [s["name"] for s in trace if s["type"] == "tool_call"] == ["echo", "fail"]


def test_command_exit_code_is_reported(tmp_path):  # X3: runs received, but the command failed -> 1
    proc = _ext(tmp_path, "sh", "-c", f'"{sys.executable}" "{HELPER}" env && exit 3')
    assert proc.returncode == 1, proc.stderr[-2000:]
    assert len(proc.stdout.strip().splitlines()) == 1 and "exited 3" in proc.stderr


def test_no_runs_received_exits_2_and_names_the_variables(tmp_path):  # X3
    proc = _ext(tmp_path, sys.executable, "-c", "pass")
    assert proc.returncode == 2 and proc.stdout == ""
    assert "No agent runs received" in proc.stderr and "OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:" in proc.stderr


def test_timeout_kills_the_command(tmp_path):  # X2
    start = time.monotonic()
    proc = _ext(tmp_path, sys.executable, "-c", "import time; time.sleep(60)", extra=("--timeout", "1"))
    assert time.monotonic() - start < 30
    assert proc.returncode == 2 and "killed" in proc.stderr  # killed, and it sent nothing


def test_usage_errors_exit_2(tmp_path):  # X3
    assert _ext(tmp_path).returncode == 2  # no command
    bad = foundry("external", "--task", "adb", "--prompt", "adb-999", "--", sys.executable, "-c", "pass")
    assert bad.returncode == 2 and "Unknown prompt id" in bad.stderr
    missing = _ext(tmp_path, str(tmp_path / "no-such-agent"))
    assert missing.returncode == 2 and "Cannot start" in missing.stderr


def test_command_is_not_run_through_a_shell(tmp_path):  # X1: argv list, so shell syntax stays literal
    marker = tmp_path / "pwned"
    proc = _ext(tmp_path, sys.executable, "-c", "import sys; print(sys.argv[1:])", f"; touch {marker}")
    assert proc.returncode == 2 and not marker.exists()
