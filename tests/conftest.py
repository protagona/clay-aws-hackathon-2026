import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "runs"
FIXTURE_NAMES = ["clean", "recovered", "failed", "injection", "hostile"]


@pytest.fixture
def fixture_run(tmp_path):
    """Copy a fixture run dir into tmp so tests can write evaluation outputs."""

    def _copy(name: str) -> Path:
        dest = tmp_path / "runs" / f"fixture-{name}"
        shutil.copytree(FIXTURES / name, dest)
        return dest

    return _copy


def foundry(*args: str, env: dict | None = None, timeout: float = 300) -> subprocess.CompletedProcess:
    """Run the real CLI entry point in a fresh process from the repo root."""
    return subprocess.run(
        [sys.executable, "-m", "foundry", *args],
        cwd=ROOT, capture_output=True, text=True, env=env, timeout=timeout,
    )
