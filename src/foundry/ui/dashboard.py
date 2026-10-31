"""The Dashboard tab (increment-6 D1-D7): a leaderboard, cost vs score, and where agents struggle, across all runs.

Inputs come from run directories, which are untrusted: the caller passes already-validated run.json and scores.json
dicts plus parsed steps, and every number here is type-checked again before it is used.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from foundry.batch import spread

MAX_COLOURED = 3  # all-pairs categorical cap (dataviz reference palette): further models fold into "Other"
SHAPES = ["circle", "square", "triangle"]
TOP = 8  # rows per "where agents struggle" list

# Scatter plot geometry, in SVG user units (the SVG scales to its container).
W, H = 960, 320
PAD_L, PAD_R, PAD_T, PAD_B = 52, 16, 14, 40


def model_key(run: dict) -> str:
    """The model as compared across runs: its id, plus " · pipeline" for multi-agent runs (increment 7, S2)."""
    model = run.get("model") if isinstance(run.get("model"), dict) else {}
    agents = run.get("agents") if isinstance(run.get("agents"), dict) else {}
    return str(model.get("id", "?")) + (" · pipeline" if agents.get("mode") == "pipeline" else "")


def _n(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _nice_axis(v: float) -> tuple[float, float]:
    """(maximum, tick step): at most 5 round steps covering v."""
    for step in (0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10):
        if v <= step * 5:
            return step * max(1, math.ceil(round(v / step, 9))), step
    return v, v


def costs(e: dict) -> tuple[float, float]:
    """(the run's own calls, its evaluation) in USD; together they are its share of the "Spent" KPI."""
    ev = (e["scores"] or {}).get("evaluation")
    return sum(_n(s.cost_usd) or 0 for s in e["steps"]), (_n(ev.get("cost_usd")) if isinstance(ev, dict) else None) or 0.0


def build(entries: list[dict]) -> dict:
    """entries: {"run_id", "run", "scores" (or None), "steps"} per readable run, as the run page reads them."""
    rows = []
    for e in entries:
        run, scores = e["run"], e["scores"]
        run_cost, eval_cost = costs(e)
        rows.append({"run_id": e["run_id"], "model": model_key(run), "prompt": run["prompt_id"],
                     "status": run["status"], "cost": run_cost, "eval_cost": eval_cost,
                     "overall": _n((scores or {}).get("overall")), "scores": scores, "steps": e["steps"]})
    scored = [r for r in rows if r["overall"] is not None]
    kpis = {"runs": len(rows), "scored": len(scored),
            "mean": sum(r["overall"] for r in scored) / len(scored) if scored else None,
            "spent": sum(r["cost"] + r["eval_cost"] for r in rows)}

    models = []
    for model in sorted({r["model"] for r in rows}):
        rs = [r for r in rows if r["model"] == model]
        sp = spread([r["overall"] for r in rs if r["overall"] is not None])
        models.append({"model": model, "runs": len(rs), "scored": sum(r["overall"] is not None for r in rs),
                       "succeeded": sum(r["status"] == "succeeded" for r in rs), "overall": sp,
                       "cost": sum(r["cost"] for r in rs) / len(rs)})
    leaderboard = sorted(models, key=lambda m: (m["overall"]["mean"] is None, -(m["overall"]["mean"] or 0), m["model"]))

    return {"kpis": kpis, "leaderboard": leaderboard, "scatter": _scatter(scored, models),
            "tool_errors": _ranked(_tool_errors(rows)), "missed": _ranked(_missed_stages(scored)),
            "criteria": _criteria(scored)}


def _scatter(scored: list[dict], models: list[dict]) -> dict:
    # Colour follows the model, never its rank: the most-run models get slots, assigned in name order.
    busiest = sorted(sorted(models, key=lambda m: -m["scored"])[:MAX_COLOURED], key=lambda m: m["model"])
    slot = {m["model"]: i for i, m in enumerate(b for b in busiest if b["scored"])}
    xmax, xstep = _nice_axis(max((r["cost"] for r in scored), default=0.0) or 0.01)
    # Dots need no zero baseline: fit the y range to the scores, in tenths, at most 0..1.
    ymin = max(0.0, math.floor((min((r["overall"] for r in scored), default=0.0) - 0.05) * 10) / 10)
    ystep = 0.1 if ymin >= 0.5 else 0.25
    ymin = min(math.floor(round(ymin / ystep, 6)) * ystep, 0.9)
    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    dots = []
    for r in scored:
        s = slot.get(r["model"])
        dots.append({"run_id": r["run_id"], "model": r["model"], "prompt": r["prompt"], "cost": r["cost"],
                     "overall": r["overall"], "series": f"s{s + 1}" if s is not None else "other",
                     "shape": SHAPES[s] if s is not None else "diamond",
                     "x": round(PAD_L + min(r["cost"] / xmax, 1) * pw, 1),
                     "y": round(PAD_T + (1 - (min(max(r["overall"], ymin), 1) - ymin) / (1 - ymin)) * ph, 1)})
    legend = [{"label": m, "series": f"s{i + 1}", "shape": SHAPES[i]} for m, i in slot.items()]
    if any(d["series"] == "other" for d in dots):
        legend.append({"label": "Other", "series": "other", "shape": "diamond"})
    xticks = [{"x": round(PAD_L + i * xstep / xmax * pw, 1), "label": f"${i * xstep:.2f}"}
              for i in range(int(round(xmax / xstep)) + 1)]
    yvals = [round(1 - i * ystep, 2) for i in range(int(round((1 - ymin) / ystep)) + 1)][::-1]
    yticks = [{"y": round(PAD_T + (1 - (v - ymin) / (1 - ymin)) * ph, 1), "label": f"{v:.2f}"} for v in yvals]
    return {"dots": dots, "legend": legend, "xticks": xticks, "yticks": yticks, "w": W, "h": H,
            "x0": PAD_L, "x1": W - PAD_R, "y0": PAD_T, "y1": H - PAD_B}


def _tool_errors(rows: list[dict]) -> Counter:
    c: Counter = Counter()
    for r in rows:
        for s in r["steps"]:
            if s.type == "tool_call" and s.status == "error":
                c[(f"{s.server}/" if s.server else "") + s.name] += 1
    return c


def _missed_stages(scored: list[dict]) -> Counter:
    c: Counter = Counter()
    for r in scored:
        seq = (r["scores"].get("workflow") or {}).get("sequence")
        if not isinstance(seq, dict) or not isinstance(seq.get("stages"), list):
            continue
        for n in seq.get("missing") or []:
            if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= len(seq["stages"]) \
                    and isinstance(seq["stages"][n - 1], list):
                tools = "|".join(str(t) for t in seq["stages"][n - 1][:4])
                c[f"{r['prompt']} · stage {n} ({tools})"] += 1
    return c


def _ranked(c: Counter) -> list[dict]:
    top = c.most_common(TOP)
    peak = top[0][1] if top else 1
    return [{"label": k, "value": v, "pct": round(v / peak * 100, 1)} for k, v in top]


def _criteria(scored: list[dict]) -> list[dict]:
    fracs: dict[str, list[float]] = defaultdict(list)
    for r in scored:
        crit = (r["scores"].get("output") or {}).get("criteria")
        for c in crit if isinstance(crit, list) else []:
            if not isinstance(c, dict) or not isinstance(c.get("id"), str):
                continue
            score, mx = _n(c.get("score")), _n(c.get("max"))
            if score is not None and mx:
                fracs[c["id"]].append(min(max(score / mx, 0), 1))
    out = [{"label": k, "value": sum(v) / len(v), "n": len(v), "pct": round(sum(v) / len(v) * 100, 1)}
           for k, v in fracs.items()]
    return sorted(out, key=lambda d: (d["value"], d["label"]))[:TOP]
