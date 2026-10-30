"""Golden live runs (G-1): recorded on Bedrock, kept for demo replay when the live path is unavailable."""

import json
import shutil

import pytest
from conftest import FIXTURES, foundry

from foundry.contract import evaluation_interrupted, validate_run_dir

GOLDEN = sorted(p for p in (FIXTURES / "golden").iterdir() if p.is_dir())


def test_golden_runs_exist():
    assert {p.name for p in GOLDEN} >= {"golden-kimi-k3-adb-001", "golden-sonnet-4-6-adb-001"}


@pytest.mark.parametrize("run_dir", GOLDEN, ids=lambda p: p.name)
def test_golden_run_valid_and_evaluated(run_dir):
    assert [v for v in validate_run_dir(run_dir) if v.severity == "error"] == []
    assert not evaluation_interrupted(run_dir)
    run = json.loads((run_dir / "run.json").read_text())
    scores = json.loads((run_dir / "scores.json").read_text())
    assert run["model"]["provider"] == "bedrock" and run["mode"] == "live"
    assert scores["output"]["judge"]["id"] == "us.anthropic.claude-opus-4-6-v1"


@pytest.mark.parametrize("run_dir", GOLDEN, ids=lambda p: p.name)
def test_golden_run_replays_offline(run_dir, tmp_path):
    """The demo fallback: re-score a recorded live run with no AWS access and no agent."""
    copy = tmp_path / run_dir.name
    shutil.copytree(run_dir, copy)
    proc = foundry("evaluate", str(copy), "--config", "config/offline.toml")
    assert proc.returncode == 0, proc.stderr
    assert (copy / "trace.jsonl").read_bytes() == (run_dir / "trace.jsonl").read_bytes()
