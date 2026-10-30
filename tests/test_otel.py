"""Tracer swap (AC16, AC16b) through the real CLI and config path, against a local OTLP endpoint."""

import json
import time

from conftest import foundry
from harness_helpers import write_config
from otlp_recorder import BlackHole, Recorder, attrs

TURNS = [{"tool": "echo", "input": {"text": "hi"}, "usage": {"in": 120, "out": 30}}, {"text": "done"}]


def _otel_cfg(tmp_path, endpoint, tracers='["jsonl", "otel"]', timeout=10):
    extra = f'\n[tracing]\ntracers = {tracers}\n[tracing.otel]\nendpoint = "{endpoint}"\nexport_timeout_s = {timeout}\n'
    return write_config(tmp_path, TURNS, extra=extra)


def _only_run(runs):
    (d,) = [p for p in runs.iterdir() if p.is_dir()]
    return d


def test_spans_exported_with_genai_attributes(tmp_path):  # AC16
    rec = Recorder()
    try:
        cfg = _otel_cfg(tmp_path, rec.endpoint)
        proc = foundry("run", "--config", str(cfg), "--prompt", "adb-001", "--runs-dir", str(tmp_path / "runs"))
        assert proc.returncode == 0, proc.stderr
        run_dir = _only_run(tmp_path / "runs")
        assert (run_dir / "trace.jsonl").read_text().strip()
        assert json.loads((run_dir / "run.json").read_text())["tracer"] == "jsonl+otel"
        spans = rec.spans()
        by_name = {s.name: attrs(s) for s in spans}
        assert "invoke_agent foundry" in by_name
        chat = sorted((attrs(s) for s in spans if s.name.startswith("chat ")), key=lambda a: a["foundry.step"])
        tool = by_name["execute_tool echo"]
        assert chat and chat[0]["gen_ai.operation.name"] == "chat"
        assert chat[0]["gen_ai.request.model"] == "scripted-test"
        assert chat[0]["gen_ai.usage.input_tokens"] == 120 and chat[0]["gen_ai.usage.output_tokens"] == 30
        assert tool["gen_ai.tool.name"] == "echo" and tool["gen_ai.operation.name"] == "execute_tool"
        root = next(s for s in spans if s.name == "invoke_agent foundry")
        assert all(s.trace_id == root.trace_id for s in spans)
        assert len(spans) == 1 + len((run_dir / "trace.jsonl").read_text().splitlines())
    finally:
        rec.close()


def test_jsonl_only_exports_nothing(tmp_path):  # AC16
    rec = Recorder()
    try:
        cfg = _otel_cfg(tmp_path, rec.endpoint, tracers='["jsonl"]')
        proc = foundry("run", "--config", str(cfg), "--prompt", "adb-001", "--runs-dir", str(tmp_path / "runs"))
        assert proc.returncode == 0, proc.stderr
        assert rec.bodies == []
    finally:
        rec.close()


def test_unreachable_endpoint_warns_once_and_is_bounded(tmp_path):  # AC16b
    hole = BlackHole()
    try:
        base_cfg = _otel_cfg(tmp_path / "base", "http://127.0.0.1:1/v1/traces", tracers='["jsonl"]')
        t0 = time.monotonic()
        assert foundry("run", "--config", str(base_cfg), "--prompt", "adb-001",
                       "--runs-dir", str(tmp_path / "base/runs")).returncode == 0
        baseline = time.monotonic() - t0

        cfg = _otel_cfg(tmp_path / "hole", hole.endpoint, timeout=2)
        t0 = time.monotonic()
        proc = foundry("run", "--config", str(cfg), "--prompt", "adb-001", "--runs-dir", str(tmp_path / "hole/runs"))
        elapsed = time.monotonic() - t0
        assert proc.returncode == 0
        warnings = [ln for ln in proc.stderr.splitlines() if ln.startswith("OTel export to")]
        assert len(warnings) == 1 and hole.endpoint in warnings[0] and "trace.jsonl is unaffected" in warnings[0]
        assert elapsed <= baseline + 2 + 2  # export_timeout_s plus process slack
        run_dir = _only_run(tmp_path / "hole/runs")
        assert json.loads((run_dir / "run.json").read_text())["status"] == "succeeded"
    finally:
        hole.close()
