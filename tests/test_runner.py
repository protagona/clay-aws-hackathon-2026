"""Harness behavior through the real runner, real Strands agent and a real (fault-injection) MCP server."""

import json
import threading
import time

from harness_helpers import PROMPT, load, output_json, run_json, steps, write_config

from foundry.harness.runner import run

FINAL = {"text": "Final plan."}


def _run(tmp_path, turns, **kw):
    cfg = load(write_config(tmp_path, turns, **kw))
    return run(cfg, PROMPT, tmp_path / "runs")


def test_happy_path_against_fault_echo(tmp_path):
    run_dir, status = _run(tmp_path, [{"tool": "echo", "input": {"text": "hi"}}, FINAL])
    assert status == "succeeded"
    s = steps(run_dir)
    assert [x["type"] for x in s] == ["llm_call", "tool_call", "llm_call"]
    assert s[1]["server"] == "fault" and s[1]["status"] == "ok" and s[1]["result_preview"] == "hi"
    for x in s:
        assert {"step", "type", "name", "started_at", "duration_ms", "status"} <= x.keys()
    assert output_json(run_dir) == {"kind": "text", "text": "Final plan.", "artifacts": []}
    rj = run_json(run_dir)
    assert rj["status"] == "succeeded" and rj["ended_at"] and rj["limits"]["max_model_calls"] == 20
    assert (run_dir / "run.json").read_text().count("\n") == 1  # single-line JSON


def test_tool_error_and_retry(tmp_path):  # AC6
    boom = {"tool": "fail", "input": {"message": "boom"}}
    run_dir, status = _run(tmp_path, [boom, boom, FINAL])
    s = steps(run_dir)
    assert s[1]["status"] == "error" and "boom" in s[1]["error"]
    assert s[3]["retry_of"] == 2 and s[3]["status"] == "error"
    assert status == "succeeded"


def test_json_status_error_detection(tmp_path):
    run_dir, _ = _run(tmp_path, [{"tool": "json_error", "input": {}}, FINAL], error_detection="json_status")
    assert steps(run_dir)[1]["status"] == "error"
    run_dir, _ = _run(tmp_path / "b", [{"tool": "json_error", "input": {}}, FINAL], error_detection="mcp")
    assert steps(run_dir)[1]["status"] == "ok"


def test_tokens_and_cost(tmp_path):  # AC7
    turns = [{"tool": "echo", "input": {"text": "x"}, "usage": {"in": 1000, "out": 100}},
             {"text": "done", "usage": {"in": 2000, "out": 200}}]
    priced = '\n[prices]\n"scripted-test" = { input = 2.0, output = 10.0 }\n'
    run_dir, _ = _run(tmp_path, turns, extra=priced)
    llm = [x for x in steps(run_dir) if x["type"] == "llm_call"]
    assert [(x["tokens_in"], x["tokens_out"]) for x in llm] == [(1000, 100), (2000, 200)]
    assert llm[0]["cost_usd"] == 0.003 and llm[1]["cost_usd"] == 0.006

    run_dir, status = _run(tmp_path / "noprice", turns)
    llm = [x for x in steps(run_dir) if x["type"] == "llm_call"]
    assert all(x["cost_usd"] is None for x in llm) and status == "succeeded"


def test_max_model_calls(tmp_path):  # AC8
    turns = [{"tool": "echo", "input": {"text": str(i)}} for i in range(10)]
    run_dir, status = _run(tmp_path, turns, limits="max_model_calls = 3")
    s = steps(run_dir)
    assert sum(x["type"] == "llm_call" for x in s) == 3
    assert any(x["type"] == "note" and x["text"] == "max_model_calls (3) reached" for x in s)
    assert status == "failed" and output_json(run_dir)["kind"] == "none"


def test_max_input_tokens(tmp_path):  # AC8
    turns = [{"tool": "echo", "input": {"text": str(i)}, "usage": {"in": 400_000, "out": 10}} for i in range(5)]
    run_dir, status = _run(tmp_path, turns, limits="max_input_tokens = 600000")
    s = steps(run_dir)
    assert any(x["type"] == "note" and x["text"] == "max_input_tokens (600000) reached" for x in s)
    assert sum(x["type"] == "llm_call" for x in s) <= 2
    assert status == "failed"


def test_timeout_records_inflight_tool(tmp_path):  # AC9
    t0 = time.monotonic()
    run_dir, status = _run(tmp_path, [{"tool": "sleep", "input": {"seconds": 30}}, FINAL], limits="max_seconds = 2")
    elapsed = time.monotonic() - t0
    assert status == "timeout"
    assert run_json(run_dir)["status"] == "timeout"
    s = steps(run_dir)
    assert s[0]["type"] == "llm_call"
    tool = [x for x in s if x["type"] == "tool_call"]
    assert tool and tool[0]["status"] == "error" and tool[0]["error"] == "cancelled: run timed out"
    assert elapsed < 2 + 5 + 3  # bound from AC9 plus MCP startup slack


def test_model_failure(tmp_path):  # AC9b: the script runs out on the 2nd model call -> the model raises
    run_dir, status = _run(tmp_path, [{"tool": "echo", "input": {"text": "x"}}])
    s = steps(run_dir)
    err = [x for x in s if x["type"] == "llm_call" and x["status"] == "error"]
    assert err and "ScriptExhausted" in err[0]["error"]
    assert status == "failed"


def test_mcp_server_fails_to_start(tmp_path):  # AC9c
    run_dir, status = _run(tmp_path, [FINAL], server_args=["--crash-on-start"])
    s = steps(run_dir)
    assert status == "failed"
    assert s[-1]["type"] == "note" and s[-1]["text"].startswith("MCP server 'fault' failed to start:")
    assert "crashing on start" in (run_dir / "mcp-fault.log").read_text()


def test_mcp_command_not_found(tmp_path):  # AC9c (missing executable)
    run_dir, status = _run(tmp_path, [FINAL], command="definitely-not-a-real-command-xyz")
    assert status == "failed"
    assert "command 'definitely-not-a-real-command-xyz' not found on PATH" in steps(run_dir)[-1]["text"]


def test_unknown_tool_is_invalid(tmp_path):  # AC9d
    run_dir, status = _run(tmp_path, [{"tool": "nope", "input": {}}, FINAL])
    s = steps(run_dir)
    assert s[1]["invalid"] is True and s[1]["status"] == "error" and "server" not in s[1]
    assert status == "succeeded"


def test_schema_invalid_args(tmp_path):  # AC9d
    run_dir, _ = _run(tmp_path, [{"tool": "echo", "input": {"text": 5}}, FINAL])
    s = steps(run_dir)[1]
    assert s["invalid"] is True and s["status"] == "error" and "Invalid arguments for echo" in s["error"]


def test_tool_result_capped_for_agent(tmp_path):
    from foundry.config import load_config
    from foundry.models.registry import build_provider  # noqa: F401  (provider built inside run)

    cfg_path = write_config(tmp_path, [{"tool": "big", "input": {"chars": 20000}}, FINAL])
    cfg = load_config(cfg_path)
    seen = {}
    import foundry.models.scripted as scripted

    orig = scripted.ScriptedModel.stream

    async def spy(self, messages, *a, **k):
        seen["messages"] = json.dumps(messages, default=str)
        async for ev in orig(self, messages, *a, **k):
            yield ev

    scripted.ScriptedModel.stream = spy
    try:
        run(cfg, PROMPT, tmp_path / "runs")
    finally:
        scripted.ScriptedModel.stream = orig
    assert "[truncated 12000 chars]" in seen["messages"]
    assert "x" * 8001 not in seen["messages"]


def test_run_json_running_during_run(tmp_path):  # AC4
    cfg = load(write_config(tmp_path, [{"tool": "sleep", "input": {"seconds": 1.5}}, FINAL]))
    seen = []

    def watch():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            for rj in (tmp_path / "runs").glob("*/run.json"):
                data = json.loads(rj.read_text())
                seen.append(data)
                if data["status"] == "running" and (rj.parent / "trace.jsonl").read_text().strip():
                    return
            time.sleep(0.05)

    t = threading.Thread(target=watch)
    t.start()
    run(cfg, PROMPT, tmp_path / "runs")
    t.join()
    running = [d for d in seen if d["status"] == "running"]
    assert running and running[0]["limits"]["max_seconds"] == 300 and "ended_at" not in running[0]
