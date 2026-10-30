import json

import pytest

from conftest import FIXTURE_NAMES, FIXTURES, foundry
from foundry.contract import Rubric, validate_run_dir


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixtures_valid(name):
    assert [v for v in validate_run_dir(FIXTURES / name) if v.severity == "error"] == []


def test_validate_cli_exit_zero_on_fixture():
    proc = foundry("validate", str(FIXTURES / "clean"))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_violations_reported_with_file_and_line(fixture_run):
    run = fixture_run("clean")
    lines = (run / "trace.jsonl").read_text().splitlines()
    bad = json.loads(lines[1])
    del bad["duration_ms"]
    bad["status"] = "maybe"
    lines[1] = json.dumps(bad)
    (run / "trace.jsonl").write_text("\n".join(lines) + "\n")
    data = json.loads((run / "run.json").read_text())
    data["status"] = "exploded"
    (run / "run.json").write_text(json.dumps(data))

    proc = foundry("validate", str(run))
    assert proc.returncode == 1
    out = proc.stdout
    assert "trace.jsonl:2: duration_ms:" in out
    assert "trace.jsonl:2: status:" in out
    assert "run.json:1: status:" in out


def test_missing_output_is_violation_unless_running(fixture_run):
    run = fixture_run("clean")
    (run / "output.json").unlink()
    assert any(v.file == "output.json" for v in validate_run_dir(run))
    data = json.loads((run / "run.json").read_text())
    data["status"] = "running"
    (run / "run.json").write_text(json.dumps(data))
    assert validate_run_dir(run) == []


def test_truncated_last_line_is_warning_only_when_running(fixture_run):
    run = fixture_run("clean")
    with open(run / "trace.jsonl", "a") as f:
        f.write('{"step": 6, "type": "tool_')
    errors = [v for v in validate_run_dir(run) if v.severity == "error"]
    assert errors and errors[0].line == 6

    data = json.loads((run / "run.json").read_text())
    data["status"] = "running"
    (run / "run.json").write_text(json.dumps(data))
    found = validate_run_dir(run)
    assert [v.severity for v in found] == ["warning"]


def test_error_step_requires_message(fixture_run):
    run = fixture_run("clean")
    lines = (run / "trace.jsonl").read_text().splitlines()
    step = json.loads(lines[1])
    step["status"] = "error"
    lines[1] = json.dumps(step)
    (run / "trace.jsonl").write_text("\n".join(lines) + "\n")
    assert any(v.line == 2 for v in validate_run_dir(run))


def test_hash_mismatch_is_warning(fixture_run):
    run = fixture_run("clean")
    (run / "rubric.json").write_text("{}")
    (run / "report.md").write_text("x")
    (run / "scores.json").write_text(json.dumps({"rubric_sha256": "0" * 64, "report_sha256": "0" * 64}))
    found = validate_run_dir(run)
    assert found and all(v.severity == "warning" for v in found)


def _rubric(**over):
    base = {"rubric_id": "r", "task_id": "adb", "version": "1", "criteria": [
        {"id": "a", "name": "A", "description": "d", "max": 4, "levels": {"1": "bad", "4": "good"}}]}
    base.update(over)
    return base


def test_rubric_rules():
    Rubric.model_validate(_rubric())
    with pytest.raises(ValueError, match="duplicate"):
        Rubric.model_validate(_rubric(criteria=_rubric()["criteria"] * 2))
    with pytest.raises(ValueError, match="levels must describe 4"):
        Rubric.model_validate(_rubric(criteria=[{**_rubric()["criteria"][0], "levels": {"1": "x"}}]))
    with pytest.raises(ValueError):
        Rubric.model_validate(_rubric(criteria=[{**_rubric()["criteria"][0], "max": 1}]))
    with pytest.raises(ValueError):
        Rubric.model_validate(_rubric(criteria=[]))


def test_symlinked_run_files_are_violations(fixture_run, tmp_path):
    run = fixture_run("clean")
    secret = tmp_path / "secret.jsonl"
    secret.write_text((run / "trace.jsonl").read_text())
    (run / "trace.jsonl").unlink()
    (run / "trace.jsonl").symlink_to(secret)
    found = validate_run_dir(run)
    assert any(v.file == "trace.jsonl" and "symlink" in v.message for v in found)
