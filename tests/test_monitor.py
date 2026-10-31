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
