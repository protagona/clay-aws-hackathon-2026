"""OpenTelemetry import (increment 3: I1-I8): evaluate agents we didn't build."""

import json
import os
import subprocess
import sys
import time
import urllib.request

import pytest
from conftest import ROOT, foundry
from harness_helpers import PROMPT, load, steps, write_config
from otlp_recorder import Recorder

from foundry.contract import validate_run_dir
from foundry.engine.importer import ImportError_, import_spans, spans_from_file

HELPER = ROOT / "tests" / "strands_otel_agent.py"


def strands_otlp(tmp_path, style: str):
    """Spans from a real Strands agent with Strands' own telemetry, as an OTLP/JSON Lines file."""
    out = tmp_path / f"strands-{style}.jsonl"
    env = {**os.environ}
    env.pop("OTEL_SEMCONV_STABILITY_OPT_IN", None)
    if style == "latest":
        env["OTEL_SEMCONV_STABILITY_OPT_IN"] = "gen_ai_latest_experimental"
    proc = subprocess.run([sys.executable, str(HELPER), "unused", "--json-out", str(out)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return out


@pytest.mark.parametrize("style", ["default", "latest"])
def test_real_strands_agent_imports_correctly(tmp_path, style):  # I1, I2, I3, I6
    src = strands_otlp(tmp_path, style)
    proc = foundry("import", str(src), "--task", "adb", "--prompt-id", "adb-001", "--runs-dir", str(tmp_path / "runs"))
    assert proc.returncode == 0, proc.stderr
    (run_dir,) = [p for p in (tmp_path / "runs").iterdir()]
    assert proc.stdout.strip() == str(run_dir)
    s = steps(run_dir)
    assert [(x["type"], x["name"], x["status"]) for x in s] == [
        ("llm_call", "model", "ok"), ("tool_call", "echo", "ok"), ("llm_call", "model", "ok"),
        ("tool_call", "fail", "error"), ("llm_call", "model", "ok")]
    assert [(x["tokens_in"], x["tokens_out"]) for x in s if x["type"] == "llm_call"] == [(500, 20), (700, 20), (900, 30)]
    assert s[1]["args"] == {"text": "hello"} and s[3]["args"] == {"message": "boom"}
    assert "boom" in s[3]["error"]
    run = json.loads((run_dir / "run.json").read_text())
    assert run["status"] == "succeeded" and run["model"]["id"] == "scripted-x"
    assert run["harness"]["name"] == "strands-agents" and run["prompt"] == "Plan a NAIP cropland dataset."
    assert run["source"]["format"] == "otlp" and len(run["source"]["trace_id"]) == 32  # hex trace id
    assert json.loads((run_dir / "output.json").read_text())["text"] == "Final plan: use NAIP."
    assert [v for v in validate_run_dir(run_dir) if v.severity == "error"] == []


def test_imported_run_evaluates_unchanged(tmp_path):  # I4
    src = strands_otlp(tmp_path, "default")
    import_spans(spans_from_file(src), tmp_path / "runs", "adb", "adb-001", str(src))
    (run_dir,) = list((tmp_path / "runs").iterdir())
    proc = foundry("evaluate", str(run_dir), "--config", "config/offline.toml")
    assert proc.returncode == 0, proc.stderr
    scores = json.loads((run_dir / "scores.json").read_text())
    assert scores["workflow"]["tool_errors"] == 1 and scores["workflow"]["tool_calls"] == 2
    assert scores["output"]["score"] is not None


def test_round_trip_with_our_otel_tracer(tmp_path):  # I5
    from google.protobuf.json_format import MessageToDict
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    from foundry.engine.harness.runner import run

    rec = Recorder()
    try:
        turns = [{"tool": "echo", "input": {"text": "x"}, "usage": {"in": 120, "out": 30}},
                 {"tool": "fail", "input": {"message": "no"}}, {"text": "done", "usage": {"in": 300, "out": 10}}]
        extra = f'\n[tracing]\ntracers = ["jsonl", "otel"]\n[tracing.otel]\nendpoint = "{rec.endpoint}"\n'
        run_dir, _ = run(load(write_config(tmp_path, turns, extra=extra)), PROMPT, tmp_path / "runs")
        src = tmp_path / "ours.jsonl"
        src.write_text("\n".join(json.dumps(MessageToDict(ExportTraceServiceRequest.FromString(b))) for b in rec.bodies))
    finally:
        rec.close()
    (imported,) = import_spans(spans_from_file(src), tmp_path / "imported", "adb", "adb-001", str(src))
    key = lambda x: (x["type"], x["name"], x["status"], x.get("tokens_in"), x.get("tokens_out"))  # noqa: E731
    original = [key(x) for x in steps(run_dir) if x["type"] in ("llm_call", "tool_call")]
    assert [key(x) for x in steps(imported)] == original


def _otlp(spans):
    def attr(k, v):
        return {"key": k, "value": {"intValue": str(v)} if isinstance(v, int) else {"stringValue": v}}

    return {"resourceSpans": [{"scopeSpans": [{"spans": [
        {"traceId": s.get("trace", "0af7651916cd43dd8448eb211c80319c"), "spanId": s["id"], "parentSpanId": s.get("parent", ""),
         "name": s["name"], "startTimeUnixNano": str(s["t"] * 10**9), "endTimeUnixNano": str(s["t"] * 10**9 + 10**8),
         "status": s.get("status", {}), "attributes": [attr(k, v) for k, v in s.get("attrs", {}).items()],
         "events": s.get("events", [])} for s in spans]}]}]}


def test_hand_written_otlp_hex_ids_subagents_and_multiple_runs(tmp_path):  # I1, I2
    doc = _otlp([
        {"id": "a1", "name": "invoke_agent planner", "t": 1, "attrs": {"gen_ai.operation.name": "invoke_agent",
         "gen_ai.request.model": "m-1", "gen_ai.system": "my-framework"},
         "events": [{"name": "gen_ai.choice", "attributes": [{"key": "message", "value": {"stringValue": "plan done"}}]}]},
        {"id": "a2", "parent": "a1", "name": "chat", "t": 2, "attrs": {"gen_ai.operation.name": "chat",
         "gen_ai.usage.prompt_tokens": 10, "gen_ai.usage.completion_tokens": 5}},
        {"id": "a3", "parent": "a1", "name": "invoke_agent checker", "t": 3, "attrs": {"gen_ai.operation.name": "invoke_agent",
         "gen_ai.agent.name": "checker"}},
        {"id": "a4", "parent": "a3", "name": "chat", "t": 4, "attrs": {"gen_ai.operation.name": "chat"}},
        {"id": "a5", "parent": "a1", "name": "execute_tool search", "t": 5, "status": {"code": "STATUS_CODE_ERROR", "message": "429 rate limited"},
         "attrs": {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.call.arguments": '{"q": "naip"}'}},
        {"id": "a6", "parent": "a1", "name": "execute_tool search", "t": 6,
         "attrs": {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.call.arguments": '{"q": "naip"}'}},
        {"id": "b1", "trace": "1111111111111111111111111111111a", "name": "invoke_agent other", "t": 7,
         "attrs": {"gen_ai.operation.name": "invoke_agent"}, "status": {"code": "STATUS_CODE_ERROR", "message": "crashed"}},
        {"id": "c1", "parent": "a1", "name": "execute_event_loop_cycle", "t": 2},
    ])
    src = tmp_path / "hand.json"
    src.write_text(json.dumps(doc))
    paths = import_spans(spans_from_file(src), tmp_path / "runs", "adb", "imported", str(src))
    assert len(paths) == 2  # two top-level agents; the nested checker is an agent_step, not a run
    s = steps(paths[0])
    assert [(x["type"], x["name"], x["status"]) for x in s] == [
        ("llm_call", "model", "ok"), ("agent_step", "checker", "ok"), ("tool_call", "search", "error"),
        ("tool_call", "search", "ok")]
    assert (s[0]["tokens_in"], s[0]["tokens_out"]) == (10, 5)  # legacy token names
    assert s[2]["error"] == "429 rate limited" and s[3]["retry_of"] == 3
    assert json.loads((paths[1] / "run.json").read_text())["status"] == "failed"
    assert paths[0].name.startswith("otel-imported-0af7651916cd")


@pytest.mark.parametrize("content,needle", [
    ("not json at all", "not JSON or JSON Lines"),
    ('{"hello": 1}', 'expected an object with "resourceSpans"'),
    (json.dumps(_otlp([{"id": "x1", "name": "chat", "t": 1, "attrs": {"gen_ai.operation.name": "chat"}}])),
     "no agent runs found"),
])
def test_bad_input_exits_2(tmp_path, content, needle):  # I1, I7
    src = tmp_path / "in.json"
    src.write_text(content)
    proc = foundry("import", str(src), "--task", "adb", "--runs-dir", str(tmp_path / "runs"))
    assert proc.returncode == 2 and needle in proc.stderr and "Traceback" not in proc.stderr


def test_size_limit_and_hostile_values(tmp_path, monkeypatch):  # I7
    import foundry.engine.importer as importer

    src = tmp_path / "big.json"
    src.write_text("x" * 2000)
    monkeypatch.setattr(importer, "MAX_FILE_BYTES", 1000)
    with pytest.raises(ImportError_, match="over the 1,000-byte limit"):
        spans_from_file(src)
    monkeypatch.undo()
    doc = _otlp([{"id": "a1", "trace": "../../etc/passwd", "name": "invoke_agent x", "t": 1,
                  "attrs": {"gen_ai.operation.name": "invoke_agent"},
                  "events": [{"name": "gen_ai.choice", "attributes": [{"key": "message", "value": {"stringValue": "y" * 50000}}]}]},
                 {"id": "a2", "trace": "../../etc/passwd", "parent": "a1", "name": "chat", "t": 2,
                  "attrs": {"gen_ai.operation.name": "chat"},
                  "events": [{"name": "gen_ai.choice", "attributes": [{"key": "message", "value": {"stringValue": "z" * 50000}}]}]}])
    src.write_text(json.dumps(doc))
    (path,) = import_spans(spans_from_file(src), tmp_path / "runs", "adb", "imported", str(src))
    assert path.parent == tmp_path / "runs" and "/" not in path.name and ".." not in path.name
    assert len(steps(path)[0]["text"]) <= 1000


def test_receiver_with_a_real_strands_agent(tmp_path):  # I8
    from foundry.engine.receiver import Receiver

    rec = Receiver(tmp_path / "runs", "adb", "adb-001", port=0, grace_s=0.5, log=lambda *_: None).start()
    try:
        assert rec.server.server_address[0] == "127.0.0.1"
        endpoint = f"http://127.0.0.1:{rec.port}/v1/traces"
        proc = subprocess.run([sys.executable, str(HELPER), endpoint], cwd=ROOT, capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr[-2000:]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not rec.written:
            time.sleep(0.1)
        (run_dir,) = rec.written
        assert [x["name"] for x in steps(run_dir)] == ["model", "echo", "model", "fail", "model"]
        assert json.loads((run_dir / "run.json").read_text())["source"]["file"] == "receiver"
        for path, body, ctype, code in (("/v1/traces", b"{not json", "application/json", 400),
                                        ("/v1/metrics", b"{}", "application/json", 404)):
            req = urllib.request.Request(f"http://127.0.0.1:{rec.port}{path}", data=body, headers={"Content-Type": ctype})
            try:
                urllib.request.urlopen(req, timeout=5)
                status = 200
            except urllib.error.HTTPError as exc:
                status = exc.code
            assert status == code
    finally:
        rec.close()
