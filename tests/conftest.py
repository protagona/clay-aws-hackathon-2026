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


@pytest.fixture(params=["laptop", "cloud"])
def backend(request, tmp_path, monkeypatch):
    """The app on a laptop and in the cloud (tests/backends.py, backend-parity ADR-6): one test, both setups."""
    from backends import make

    return make(request, tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _no_path_check_network(request, monkeypatch):
    """The path check makes real S3 calls; tests turn it off (env reaches subprocesses) and fail loudly on a real call.

    `network` tests keep the real lister; tests of the check pass their own lister or clear the variable.
    """
    monkeypatch.setenv("FOUNDRY_PATH_CHECK", "off")
    if request.node.get_closest_marker("network") is None:
        from foundry.engine.evaluate import paths

        def _refuse():
            raise AssertionError("path check tried real S3 in a non-network test; pass a fake lister")

        monkeypatch.setattr(paths, "default_lister", _refuse)
        monkeypatch.setattr(paths, "requester_pays_lister", _refuse)  # signed and billed: never in unit tests
