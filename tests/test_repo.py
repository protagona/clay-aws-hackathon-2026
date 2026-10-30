import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_license_is_apache_2():
    text = (ROOT / "LICENSE").read_text()
    assert "Apache License" in text and "Version 2.0, January 2004" in text


def test_pyproject_declares_apache_2():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert meta["project"]["license"] == "Apache-2.0"
