"""The real `foundry` entry point in a fresh process (AC1, AC2, AC5, AC15)."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import ROOT, foundry
from harness_helpers import write_config


def _env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("FOUNDRY_")}
    env.update(extra)
    return env


def _run_dirs(runs: Path) -> list[Path]:
    return sorted(p for p in runs.iterdir() if p.is_dir()) if runs.exists() else []


def test_unknown_prompt_exits_2_without_run_dir(tmp_path):  # AC1
    runs = tmp_path / "runs"
    proc = foundry("run", "--config", "config/offline.toml", "--prompt", "adb-999", "--runs-dir", str(runs))
    assert proc.returncode == 2
    assert "Unknown prompt id 'adb-999' for task 'adb'. Available: adb-001, adb-002, adb-003, adb-004, adb-005, adb-006" in proc.stderr
    assert _run_dirs(runs) == []


def test_missing_config_exits_2(tmp_path):  # AC1
    proc = foundry("run", "--config", "config/nope.toml", "--prompt", "adb-001", "--runs-dir", str(tmp_path))
    assert proc.returncode == 2 and "config/nope.toml" in proc.stderr and "not found" in proc.stderr


def test_invalid_config_names_key(tmp_path):  # AC1
    cfg = tmp_path / "bad.toml"
    cfg.write_text((ROOT / "config/offline.toml").read_text().replace('provider = "offline"', 'provider = "foo"', 1))
    proc = foundry("run", "--config", str(cfg), "--prompt", "adb-001", "--runs-dir", str(tmp_path / "runs"))
    assert proc.returncode == 2
    assert "[judge] provider" in proc.stderr and "'foo'" in proc.stderr
    assert _run_dirs(tmp_path / "runs") == []


def test_bad_prompt_id_line_is_config_error(tmp_path):  # AC1
    home = tmp_path / "home"
    shutil.copytree(ROOT / "config", home / "config")
    (home / "tasks/adb").mkdir(parents=True)
    (home / "tasks/adb/prompts.jsonl").write_text('{"prompt_id": "ok-1", "prompt": "x"}\n{"prompt_id": "../evil", "prompt": "y"}\n')
    proc = foundry("run", "--config", "config/offline.toml", "--prompt", "ok-1", "--runs-dir", str(tmp_path / "runs"),
                   env=_env(FOUNDRY_HOME=str(home)))
    assert proc.returncode == 2
    assert "prompts.jsonl:2" in proc.stderr and "prompt_id" in proc.stderr


def test_invalid_run_id_rejected(tmp_path):
    proc = foundry("run", "--config", "config/offline.toml", "--prompt", "adb-001", "--runs-dir", str(tmp_path),
                   "--run-id", "../x")
    assert proc.returncode == 2


def test_exit_1_for_failed_run(tmp_path):  # AC2
    cfg = write_config(tmp_path, [{"text": "x"}], server_args=["--crash-on-start"])
    runs = tmp_path / "runs"
    proc = foundry("run", "--config", str(cfg), "--prompt", "adb-001", "--runs-dir", str(runs))
    assert proc.returncode == 1
    (run_dir,) = _run_dirs(runs)
    assert proc.stdout.strip() == str(run_dir)
    assert json.loads((run_dir / "run.json").read_text())["status"] == "failed"


def test_exit_0_for_succeeded_run_with_fault_server(tmp_path):  # AC2 (no network)
    cfg = write_config(tmp_path, [{"tool": "echo", "input": {"text": "x"}}, {"text": "done"}])
    proc = foundry("run", "--config", str(cfg), "--prompt", "adb-001", "--runs-dir", str(tmp_path / "runs"),
                   "--run-id", "my-run-1")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().endswith("my-run-1")


def test_run_evaluate_one_command(tmp_path):  # D-8: run, trace, report in one command
    cfg = write_config(tmp_path, [{"tool": "echo", "input": {"text": "x"}}, {"text": "done"}])
    proc = foundry("run", "--config", str(cfg), "--prompt", "adb-001", "--runs-dir", str(tmp_path / "runs"),
                   "--run-id", "r1", "--evaluate")
    assert proc.returncode == 0, proc.stderr
    run_dir, report, scores = proc.stdout.strip().splitlines()
    assert run_dir.endswith("r1") and report == str(Path(run_dir) / "report.md") and scores.startswith("overall ")
    assert (Path(run_dir) / "scores.json").exists() and (Path(run_dir) / "report.md").exists()


def test_run_evaluate_scores_failed_run_and_keeps_exit_1(tmp_path):
    cfg = write_config(tmp_path, [{"text": "x"}], server_args=["--crash-on-start"])
    proc = foundry("run", "--config", str(cfg), "--prompt", "adb-001", "--runs-dir", str(tmp_path / "runs"),
                   "--evaluate")
    assert proc.returncode == 1
    (run_dir,) = _run_dirs(tmp_path / "runs")
    assert json.loads((run_dir / "scores.json").read_text())["workflow"]["components"]["completion"] == 0


@pytest.mark.network
def test_run_offline_real_roda(tmp_path):  # AC5
    runs = tmp_path / "runs"
    proc = foundry("run", "--config", "config/offline.toml", "--prompt", "adb-001", "--runs-dir", str(runs))
    assert proc.returncode == 0, proc.stderr
    (run_dir,) = _run_dirs(runs)
    assert proc.stdout.strip() == str(run_dir)
    steps = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()]
    assert any(s["type"] == "llm_call" for s in steps)
    roda = [s for s in steps if s["type"] == "tool_call"]
    assert roda and all(s["server"] == "roda" and s["status"] == "ok" for s in roda)
    servers = [m["name"] for m in json.loads((run_dir / "run.json").read_text())["mcp_servers"]]
    assert servers == ["roda", "stac", "embeddings"]
    for s in steps:
        assert {"step", "type", "name", "started_at", "duration_ms", "status"} <= s.keys()
    assert foundry("validate", str(run_dir)).returncode == 0


@pytest.mark.network
def test_model_swap_no_code_change(tmp_path):  # AC15
    before = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                            capture_output=True, text=True).stdout
    models = []
    for cfg in ("config/offline.toml", "config/offline-alt.toml"):
        runs = tmp_path / Path(cfg).stem
        proc = foundry("run", "--config", cfg, "--prompt", "adb-001", "--runs-dir", str(runs))
        assert proc.returncode == 0, proc.stderr
        (run_dir,) = _run_dirs(runs)
        models.append(json.loads((run_dir / "run.json").read_text())["model"]["id"])
        assert foundry("validate", str(run_dir)).returncode == 0
    assert models == ["scripted-a", "scripted-b"]
    after = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                           capture_output=True, text=True).stdout
    assert before == after
    a, b = ((ROOT / c).read_text().splitlines() for c in ("config/offline.toml", "config/offline-alt.toml"))
    changed = [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]
    section = None
    for i, line in enumerate(a):
        if line.startswith("["):
            section = line.split("]")[0] + "]"
        if i in changed:
            assert section == "[model]"


@pytest.mark.network
def test_roda_json_status_error_detected(tmp_path):
    runs = tmp_path / "runs"
    proc = foundry("run", "--config", "config/offline.toml", "--prompt", "adb-002", "--runs-dir", str(runs))
    assert proc.returncode == 0, proc.stderr
    (run_dir,) = _run_dirs(runs)
    steps = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()]
    errs = [s for s in steps if s["type"] == "tool_call" and s["status"] == "error"]
    assert errs and "Dataset not found" in errs[0]["error"]


def test_unwritable_runs_dir_exits_2_with_message(tmp_path):
    runs = tmp_path / "ro"
    runs.mkdir()
    runs.chmod(0o555)
    try:
        cfg = write_config(tmp_path, [{"text": "x"}])
        proc = foundry("run", "--config", str(cfg), "--prompt", "adb-001", "--runs-dir", str(runs))
        assert proc.returncode == 2 and "Cannot write the run directory" in proc.stderr
        assert "Traceback" not in proc.stderr
    finally:
        runs.chmod(0o755)
