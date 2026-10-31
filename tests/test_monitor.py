"""The run page monitor's numbers (increment-5 M3, M4): gauges against limits and waterfall geometry."""

from datetime import datetime, timezone

from foundry.contract import Step
from foundry.ui.monitor import monitor

T0 = "2026-10-30T10:00:00.000Z"


def _step(n, type_, at_s, ms, status="ok", **kw):
    return Step(step=n, type=type_, name=kw.pop("name", "model" if type_ == "llm_call" else "search"),
                started_at=f"2026-10-30T10:00:{at_s:06.3f}Z", duration_ms=ms, status=status, **kw)


def _run(**kw):
    return {"status": "succeeded", "started_at": T0, "ended_at": "2026-10-30T10:00:10.000Z",
            "limits": {"max_model_calls": 4, "max_input_tokens": 1000, "max_seconds": 100}, **kw}


STEPS = [
    _step(1, "llm_call", 0, 2000, tokens_in=400, tokens_out=50, cost_usd=0.01),
    _step(2, "tool_call", 2, 1000, server="roda"),
    _step(3, "tool_call", 2, 500, status="error", server="stac", error="boom"),  # parallel with 2
    _step(4, "llm_call", 3, 5000, tokens_in=400, tokens_out=80, cost_usd=0.02),
    _step(5, "note", 8, 0, name="limit", text="x"),
]


def test_gauges_against_limits():
    m = monitor(_run(), STEPS, live=False)
    calls, time_, toks = m["gauges"]
    assert (calls["value"], calls["max"], calls["pct"], calls["level"]) == ("2", "4", 50.0, "ok")
    assert (time_["value"], time_["max"], time_["level"]) == ("10s", "100s", "ok")
    assert (toks["value"], toks["max"], toks["pct"], toks["level"]) == ("800", "1.0k", 80.0, "warn")
    assert m["cost"] == 0.03 and m["tokens_out"] == "130" and (m["tools_ok"], m["tools_err"]) == (1, 1)
    assert m["servers"] == [("roda", 1), ("stac", 1)] and m["elapsed"] == "00:10"


def test_over_limit_is_bad_and_missing_limit_has_no_bar():
    m = monitor(_run(limits={"max_model_calls": 2, "max_seconds": "x"}), STEPS, live=False)
    calls, time_, toks = m["gauges"]
    assert calls["level"] == "bad" and calls["pct"] == 100.0
    assert time_["pct"] is None and time_["max"] is None and toks["pct"] is None


def test_waterfall_geometry_shows_parallel_calls():
    m = monitor(_run(), STEPS, live=False)
    rows = {r["step"]: r for r in m["rows"]}
    assert 5 not in rows  # notes go to the feed only
    assert rows[1]["left"] == 0 and rows[1]["width"] == 20.0 and rows[1]["kind"] == "model"
    assert rows[2]["left"] == rows[3]["left"] == 20.0 and rows[3]["error"] and rows[2]["label"] == "roda/search"
    assert rows[4]["left"] == 30.0 and rows[4]["width"] == 50.0
    assert [t["label"] for t in m["axis"]] == ["0s", "5s", "10s"] and m["working"] is None
    assert [f["s"].step for f in m["feed"]] == [5, 4, 3, 2, 1]


def test_live_run_has_working_bar_clock_and_new_steps():
    now = datetime(2026, 10, 30, 10, 0, 20, tzinfo=timezone.utc)
    run = _run(status="running")
    run.pop("ended_at")
    m = monitor(run, STEPS[:2], live=True, now=now, seen=1)
    assert m["elapsed"] == "00:20" and m["start_ms"] == int(datetime(2026, 10, 30, 10, tzinfo=timezone.utc).timestamp() * 1000)
    assert m["working"] == {"left": 15.0, "width": 85.0}  # from the end of step 2 (3 s) to now (20 s)
    assert [(f["s"].step, f["new"]) for f in m["feed"]] == [(2, True), (1, False)] and m["seen"] == 2


def test_untrusted_run_fields_do_not_crash():
    m = monitor({"status": "running", "started_at": 5, "limits": "nope"}, STEPS, live=True)
    assert m["start_ms"] is None and all(g["pct"] is None for g in m["gauges"])
    assert monitor({}, [], live=False)["rows"] == []


# --- increment 7: agent bands -----------------------------------------------------------------------

PIPE = {"mode": "pipeline"}


def _agent(n, at_s, name):
    return Step(step=n, type="agent_step", name=name, started_at=f"2026-10-30T10:00:{at_s:06.3f}Z", duration_ms=0,
                status="ok")


BAND_STEPS = [
    _agent(1, 0, "planner"),
    _step(2, "llm_call", 0, 1000, parent=1),
    _agent(3, 1, "executor"),
    _step(4, "llm_call", 1, 2000, parent=3),
    _step(5, "tool_call", 3, 1000, parent=3, server="roda"),
    _agent(6, 4, "checker"),
    _step(7, "llm_call", 4, 1000, parent=6),
    Step(step=8, type="note", name="verdict", text="revise", started_at="2026-10-30T10:00:05.000Z", duration_ms=0,
         status="ok", parent=6),
]


def test_agent_bands():  # U1, U2
    m = monitor(_run(agents=PIPE), BAND_STEPS, live=False)
    rows = [(r["kind"], r["label"], r.get("indent", False)) for r in m["rows"]]
    assert rows == [("agent", "planner", False), ("model", "model", True), ("agent", "executor", False),
                    ("model", "model", True), ("tool", "roda/search", True), ("agent", "checker", False),
                    ("model", "model", True)]
    executor = m["rows"][2]
    assert (executor["calls"], executor["secs"], executor["left"]) == (2, 3.0, 10.0)  # 1 s .. 4 s on a 10 s axis
    assert m["rows"][5]["verdict"] == "revise"
    agents = {f["s"].step: f["agent"] for f in m["feed"]}
    assert agents[5] == "executor" and agents[8] == "checker" and agents[1] is None
    assert [f["s"].step for f in m["feed"] if f["divider"]] == [6, 3, 1]


def test_single_agent_rows_unchanged():  # U1: no agents.mode -> flat rows, agent_steps (e.g. imported) skipped
    flat = monitor(_run(), BAND_STEPS, live=False)
    assert all(r["kind"] in ("model", "tool") and not r["indent"] for r in flat["rows"]) and len(flat["rows"]) == 4
    assert not any(f["divider"] or f["agent"] for f in flat["feed"])


def test_live_band_extends_to_now_and_empty_agent_has_a_band():  # U3
    now = datetime(2026, 10, 30, 10, 0, 20, tzinfo=timezone.utc)
    run = _run(status="running", agents=PIPE)
    run.pop("ended_at")
    m = monitor(run, BAND_STEPS[:6], live=True, now=now)  # the checker has just started, no calls yet
    checker = m["rows"][-1]
    assert checker["kind"] == "agent" and checker["calls"] == 0 and checker["left"] == 20.0  # its own start: 4 s / 20 s
    assert checker["width"] == 80.0  # reaches now (20 s)
