"""The dashboard page: every run's numbers (foundry.services.stats), filtered by the rubric that scored them."""

from __future__ import annotations

from foundry.services import rubrics, runs, stats
from foundry.services.workspace import Workspace


def dashboard_view(ws: Workspace, rubric: str = "active") -> dict:
    entries = runs.entries(ws)
    # Scores from different rubrics are not comparable, so the default is the active rubric(s) only.
    options, active = rubrics.rubric_options(ws, entries)
    chosen = active if rubric == "active" else (None if rubric == "all" else
                                                 ({rubric} if any(o["sha"] == rubric for o in options) else active))
    shown = entries if chosen is None else [e for e in entries if (e["scores"] or {}).get("rubric_sha256") in chosen]
    mixed = chosen is None and len({(e["scores"] or {}).get("rubric_sha256") for e in entries if e["scores"]}) > 1
    scored_by: dict[str, str] = {}  # rubric sha -> one run scored with it, to open that rubric's copy (increment 10)
    for e in entries:
        sha = (e["scores"] or {}).get("rubric_sha256")
        if isinstance(sha, str):
            scored_by.setdefault(sha, e["run_id"])
    return {"d": stats.build(shown), "rubric_options": options, "scored_by": scored_by,
            "rubric_choice": rubric if rubric in ("active", "all") or chosen is not active else "active",
            "mixed": mixed, "total_runs": len(entries)}
