"""The run page's live monitor (increment-5): gauges against the run's limits, a waterfall of steps, and a feed.

Everything here is computed from the run's own files, which are untrusted: numbers are type-checked and text is
left as plain text: only api/render.py turns run text into HTML, and the web app shows everything else as
text.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

WARN_AT = 0.75  # a gauge turns amber at this fraction of its limit, red at 1.0
MIN_BAR_PCT = 0.6  # so a 10 ms tool call is still visible on a 5-minute axis


def _ts(iso) -> datetime | None:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _n(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def tokens(n: float | None) -> str:
    if n is None:
        return "—"
    return f"{n / 1000:.1f}k" if n >= 1000 else f"{n:.0f}"


def clock(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def _gauge(label: str, value: float, limit, shown: str, max_shown: str | None = None) -> dict:
    limit = _n(limit)
    frac = value / limit if limit else None
    level = None if frac is None else "bad" if frac >= 1 else "warn" if frac >= WARN_AT else "ok"
    return {"label": label, "value": shown, "max": max_shown if limit else None, "level": level,
            "pct": None if frac is None else round(min(frac, 1.0) * 100, 1)}


def _axis(span_s: float) -> list[dict]:
    """Up to five ticks at round intervals."""
    step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if span_s / s <= 4), 1200)
    ticks, t = [], 0
    while t <= span_s + 1e-9:
        ticks.append({"pct": round(t / span_s * 100, 2), "label": f"{t}s" if step < 60 else f"{t // 60}m"})
        t += step
    return ticks


def monitor(run: dict, steps: list, *, live: bool, now: datetime | None = None, seen: int | None = None) -> dict:
    """The template context for `_monitor.html`. `seen` is the highest step the browser already shows."""
    now = now or datetime.now(timezone.utc)
    t0 = _ts(run.get("started_at"))
    end = _ts(run.get("ended_at"))
    limits = run.get("limits") if isinstance(run.get("limits"), dict) else {}

    calls = [s for s in steps if s.type in ("llm_call", "tool_call")]
    model = [s for s in calls if s.type == "llm_call"]
    tools = [s for s in calls if s.type == "tool_call"]
    tokens_in = sum(_n(s.tokens_in) or 0 for s in model)
    tokens_out = sum(_n(s.tokens_out) or 0 for s in model)
    costs = [_n(s.cost_usd) for s in steps if _n(s.cost_usd) is not None]

    # Waterfall geometry, in seconds from the run's start.
    bars, last_end = [], 0.0
    for s in calls:
        st = _ts(s.started_at)
        off = (st - t0).total_seconds() if st and t0 else last_end
        dur = max(0.0, (_n(s.duration_ms) or 0) / 1000)
        bars.append({"s": s, "off": max(0.0, off), "dur": dur})
        last_end = max(last_end, max(0.0, off) + dur)
    elapsed = None
    if t0:
        elapsed = ((end or now) - t0).total_seconds() if (end or live) else last_end
    span = max(elapsed or 0, last_end, 1.0)

    def bar(s, off: float, dur: float, indent: bool = False) -> dict:
        return {"step": s.step, "kind": "model" if s.type == "llm_call" else "tool",
                "label": "model" if s.type == "llm_call" else (f"{s.server}/" if s.server else "") + s.name,
                "error": s.status == "error", "ms": _n(s.duration_ms), "new": seen is not None and s.step > seen,
                "left": round(off / span * 100, 2), "width": round(max(dur / span * 100, MIN_BAR_PCT), 2),
                "indent": indent}

    agents = run.get("agents") if isinstance(run.get("agents"), dict) else {}
    pipeline = agents.get("mode") == "pipeline"
    markers = {s.step: s for s in steps if s.type == "agent_step"} if pipeline else {}
    agent_of = {s.step: markers[s.parent].name for s in steps if s.parent in markers}
    if markers:
        starts = {n: max(0.0, (_ts(m.started_at) - t0).total_seconds()) if _ts(m.started_at) and t0 else last_end
                  for n, m in markers.items()}
        rows = _bands(steps, bars, markers, starts, bar, span, elapsed if live and run.get("status") == "running"
                      else None, seen)
    else:
        rows = [bar(b["s"], b["off"], b["dur"]) for b in bars]
    working = None
    if live and run.get("status") == "running":
        working = {"left": round(last_end / span * 100, 2),
                   "width": round(max((span - last_end) / span * 100, MIN_BAR_PCT), 2)}

    gauges = [
        _gauge("Model calls", len(model), limits.get("max_model_calls"), f"{len(model)}",
               f"{_n(limits.get('max_model_calls')) or 0:g}"),
        _gauge("Time", elapsed or 0, limits.get("max_seconds"), f"{elapsed or 0:.0f}s",
               f"{_n(limits.get('max_seconds')) or 0:g}s"),
        _gauge("Input tokens", tokens_in, limits.get("max_input_tokens"), tokens(tokens_in),
               tokens(_n(limits.get("max_input_tokens")))),
    ]
    errors = sum(s.status == "error" for s in tools)
    per_server = Counter(s.server or "?" for s in tools)
    top = max((s.step for s in steps), default=0)
    return {
        "live": live, "status": run.get("status"), "elapsed": clock(elapsed),
        "start_ms": int(t0.timestamp() * 1000) if t0 and live and run.get("status") == "running" else None,
        "gauges": gauges, "cost": sum(costs) if costs else None, "tokens_out": tokens(tokens_out),
        "tools_ok": len(tools) - errors, "tools_err": errors,
        "servers": sorted(per_server.items(), key=lambda kv: (-kv[1], kv[0])),
        "rows": rows, "working": working, "axis": _axis(span),
        "feed": [{"s": s, "new": seen is not None and s.step > seen, "agent": agent_of.get(s.step),
                  "divider": s.step in markers} for s in reversed(steps)],
        "seen": top,
    }


def _bands(steps, bars, markers, starts: dict, bar, span: float, now_s: float | None, seen) -> list[dict]:
    """Pipeline waterfall (increment 7, U1): each agent_step is a header row spanning its descendants, which follow
    it indented, in step order. Parentless calls keep their own position."""
    geo = {b["s"].step: b for b in bars}
    kids: dict[int, list] = {}
    for s in steps:
        if s.parent in markers:
            kids.setdefault(s.parent, []).append(s)
    last_marker = max(markers) if markers else None
    rows = []
    for s in steps:
        if s.step in markers:
            own = [geo[c.step] for c in kids.get(s.step, []) if c.step in geo]
            if own:
                lo, hi = min(b["off"] for b in own), max(b["off"] + b["dur"] for b in own)
            else:  # no calls yet (or ever): a minimum-width band at the agent's start
                lo = hi = starts[s.step]
            if now_s is not None and s.step == last_marker:  # the running agent's band reaches now (U3)
                hi = max(hi, now_s)
            verdict = next((c.text for c in kids.get(s.step, []) if c.type == "note" and c.name == "verdict"), None)
            rows.append({"step": s.step, "kind": "agent", "label": s.name, "calls": len(own),
                         "secs": round(hi - lo, 1), "verdict": verdict, "error": False, "ms": None,
                         "new": seen is not None and s.step > seen,
                         "left": round(lo / span * 100, 2), "width": round(max((hi - lo) / span * 100, MIN_BAR_PCT), 2)})
            rows += [bar(c, geo[c.step]["off"], geo[c.step]["dur"], indent=True)
                     for c in kids.get(s.step, []) if c.step in geo]
        elif s.parent not in markers and s.step in geo:
            rows.append(bar(s, geo[s.step]["off"], geo[s.step]["dur"]))
    return rows
