"""Process robustness (increment 2: R1-R3)."""

import json
import os
import signal
import subprocess
import sys
import time
import uuid

from conftest import ROOT
from harness_helpers import write_config


def _pids(marker: str) -> list[int]:
    out = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split()
    return [int(p) for p in out]


def _wait(pred, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.1)
    return False


def test_sigkilled_run_leaves_no_mcp_server_behind(tmp_path):  # R1
    marker = f"marker-{uuid.uuid4().hex}"
    cfg = write_config(tmp_path, [{"tool": "sleep", "input": {"seconds": 60}}, {"text": "x"}], server_args=[marker])
    runs = tmp_path / "runs"
    proc = subprocess.Popen([sys.executable, "-m", "foundry", "run", "--config", str(cfg), "--prompt", "adb-001",
                             "--runs-dir", str(runs)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        def tool_started():
            traces = list(runs.glob("*/trace.jsonl"))
            return bool(traces) and '"llm_call"' in traces[0].read_text()
        assert _wait(tool_started, 60), "run never reached the tool call"
        assert _wait(lambda: len(_pids(marker)) >= 1, 10), "fault server not running"
        time.sleep(0.5)  # the sleep tool is now in flight
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=5)
        assert _wait(lambda: not _pids(marker), 5), f"MCP server outlived the harness: {_pids(marker)}"
    finally:
        if proc.poll() is None:
            proc.kill()
        for pid in _pids(marker):
            os.kill(pid, signal.SIGKILL)


def test_reaper_passes_exit_code_and_usage():
    assert subprocess.run([sys.executable, "-m", "foundry.harness.reaper", "--", sys.executable, "-c",
                           "raise SystemExit(7)"]).returncode == 7
    assert subprocess.run([sys.executable, "-m", "foundry.harness.reaper"], capture_output=True).returncode == 2


def test_normal_run_still_logs_server_stderr(tmp_path):  # R2
    from harness_helpers import PROMPT, load

    from foundry.harness.runner import run

    run_dir, status = run(load(write_config(tmp_path, [{"text": "done"}], server_args=["--crash-on-start"])),
                          PROMPT, tmp_path / "runs")
    assert status == "failed" and "crashing on start" in (run_dir / "mcp-fault.log").read_text()
    assert json.loads((run_dir / "run.json").read_text())["status"] == "failed"


def test_crashing_server_prints_no_runtime_warning(tmp_path):  # R3
    from conftest import foundry

    cfg = write_config(tmp_path, [{"text": "x"}], server_args=["--crash-on-start"])
    proc = foundry("run", "--config", str(cfg), "--prompt", "adb-001", "--runs-dir", str(tmp_path / "runs"))
    assert proc.returncode == 1
    assert "RuntimeWarning" not in proc.stderr and "never awaited" not in proc.stderr
