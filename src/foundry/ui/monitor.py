"""The run page's live monitor (increment-5): gauges against the run's limits, a waterfall of steps, and a feed.

Everything here is computed from the run's own files, which are untrusted: numbers are type-checked and text is
left to Jinja's autoescaping.
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
    rows = []
    for b in bars:
        s = b["s"]
        rows.append({"step": s.step, "kind": "model" if s.type == "llm_call" else "tool",
                     "label": "model" if s.type == "llm_call" else (f"{s.server}/" if s.server else "") + s.name,
                     "error": s.status == "error", "ms": _n(s.duration_ms), "new": seen is not None and s.step > seen,
                     "left": round(b["off"] / span * 100, 2),
                     "width": round(max(b["dur"] / span * 100, MIN_BAR_PCT), 2)})
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
        "feed": [{"s": s, "new": seen is not None and s.step > seen} for s in reversed(steps)],
        "seen": top,
    }
