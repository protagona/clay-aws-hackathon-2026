"""A run's board card and summary, built from its own files only (backend-parity design ADR-2): the laptop builds
them on every board read, the cloud when it records a run (CloudState.record_run) and stores them in the run index.
One builder, so both boards show the same cards. Live state (being scored, interrupted) is added by runs.board.

A new card field: compute it in `card_for` or `card_extras`, add it to CARD_FIELDS and to api/schemas.py `Card`;
stored cloud cards get it on their next record, or all at once with `scripts/cloud.sh reindex`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from foundry.contract import evaluation_interrupted
from foundry.data import runs as data_runs
from foundry.services import stats
from foundry.services.workspace import Workspace

CARD_FIELDS = ("unreadable", "run_id", "prompt_id", "model", "headline", "sub", "status", "when", "footer", "started_at",
               "overall", "workflow", "output", "cost", "agents_mode", "agents_started", "reason")


def short(score) -> str:
    return "—" if score is None else f"{score:.2f}".removeprefix("0")


def when(iso: str | None) -> str:
    try:
        return datetime.strptime(iso.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z").strftime("%m-%d %H:%M")
    except (AttributeError, ValueError):
        return ""


def card_for(ws: Workspace, d: Path) -> dict:  # noqa: ARG001 (ws: the signature callers use)
    """One run directory's board card basics, from its files alone. Whether it's being scored is live state the
    board adds (runs.board), so a stored card never goes stale on it."""
    run = data_runs.read_run(d)
    if run is None:
        return {"unreadable": True, "name": d.name, "started_at": ""}
    scores = data_runs.read_scores(d)
    status = run["status"]
    if status == "running":
        headline = "running"
    elif scores is None:
        headline = "not evaluated" if not data_runs.has_scores_file(d) else "incomplete"
    elif evaluation_interrupted(d) or scores["overall"] is None:
        headline = "incomplete"
    else:
        headline = f"{scores['overall']:.2f}"
    sub = ""
    if scores is not None and headline != "incomplete" and scores["workflow"]["score"] is not None:
        sub = f"W{short(scores['workflow']['score'])} O{short(scores['output']['score'])}"
    started = run.get("started_at") if isinstance(run.get("started_at"), str) else ""
    return {"unreadable": False, "run_id": d.name, "prompt_id": run["prompt_id"],
            "model": stats.model_key(run), "headline": headline, "sub": sub, "status": status,
            "when": when(started), "footer": f"{run.get('task_id', '?')} · …{d.name[-4:]}",
            "started_at": started}


INTERRUPTED_REASON = "Its worker stopped before finishing"
_LIMITS = (("max_model_calls (", "Hit the model-call limit ({})"), ("max_input_tokens (", "Hit the input-token limit ({})"),
           ("max_seconds (", "Hit the time limit ({} s)"))


def _exc_name(error) -> str:
    return str(error or "").split(":", 1)[0].strip()[:60] or "unknown"


def failure_reason(status, steps: list[dict]) -> str | None:
    """Why a run failed, in a few plain words, for its board card (board-attention spec B1); None unless it failed,
    timed out or was interrupted. Only the trace's note names, limit texts and exception names are used, never
    prompt or model text. The first matching rule wins."""
    if status == "interrupted":
        return INTERRUPTED_REASON
    if status not in ("failed", "timeout"):
        return None
    notes = [(s.get("name"), str(s.get("text") or "")) for s in steps if s.get("type") == "note"]
    for name, text in notes:
        if name == "limit":
            for prefix, phrase in _LIMITS:
                if text.startswith(prefix):
                    return phrase.format(text[len(prefix):].split(")", 1)[0])
            if text.startswith("hard timeout"):
                return "Timed out, and the agent didn't stop"
    if status == "timeout":
        return "Hit the time limit"
    if any(name == "mcp_start" for name, _ in notes):
        return "A tool server didn't start"
    errors = [s for s in steps if s.get("status") == "error" and s.get("type") in ("note", "llm_call")
              and (s.get("type") == "llm_call" or s.get("name") == "agent_error")]
    if any("MaxTokensReachedException" in str(s.get("error") or "") for s in errors):
        return "Hit the output-token limit; the answer was cut off"
    if errors:
        last = errors[-1]
        return f"{'Model call failed' if last.get('type') == 'llm_call' else 'Agent error'}: {_exc_name(last.get('error'))}"
    return "No final answer"


def card_extras(ws: Workspace, run_id: str) -> dict:
    """Numbers a board card shows beyond the card basics (all from the run's own files)."""
    path = ws.runs_dir / run_id
    sc = data_runs.read_scores(path) or {}
    run = data_runs.read_run(path) or {}
    agents = run.get("agents") if isinstance(run.get("agents"), dict) else {}
    cost = None
    wf = sc.get("workflow") if isinstance(sc.get("workflow"), dict) else {}
    if data_runs.num(wf.get("cost_usd")) is not None:  # never a bool (increment 17)
        cost = wf["cost_usd"]
    mode = agents.get("mode") if isinstance(agents.get("mode"), str) else None
    started: list[str] = []
    status = run.get("status")
    live_pipeline = mode == "pipeline" and status == "running"
    failed = status in ("failed", "timeout", "interrupted")
    trace = data_runs.read_trace_lenient(path) if live_pipeline or cost is None or failed else []
    if live_pipeline or cost is None:  # unscored runs: cost from the trace; live pipeline runs: agents started so far
        total, seen_cost = 0.0, False
        for s in trace:
            if live_pipeline and s.get("type") == "agent_step" and isinstance(s.get("name"), str):
                started.append(s["name"])
            c = s.get("cost_usd")
            if isinstance(c, (int, float)) and not isinstance(c, bool):
                total, seen_cost = total + c, True
        if cost is None and seen_cost:
            cost = round(total, 6)
    return {"overall": sc.get("overall"), "workflow": wf.get("score"),
            "output": (sc.get("output") or {}).get("score") if isinstance(sc.get("output"), dict) else None,
            "cost": cost, "agents_mode": mode, "agents_started": started[:8],
            **({"reason": failure_reason(status, trace)} if failed else {})}


def entry_for(d: Path) -> dict | None:
    """One readable run with its scores (None if never or partly scored) and steps; None if unreadable."""
    run = data_runs.read_run(d)
    if run is None:
        return None
    scores = data_runs.read_scores(d)
    if scores is not None and evaluation_interrupted(d):
        scores = None
    return {"run_id": d.name, "run": run, "scores": scores, "steps": data_runs.read_steps_safely(d)}


def summary_for(d: Path) -> dict | None:
    """What the cloud run index keeps per run, with `entries()` semantics (scores dropped when interrupted; spend =
    the run's calls plus its evaluation): enough for the board's KPIs and the prompt pages without the run's files."""
    e = entry_for(d)
    if e is None:
        return None
    sc = e["scores"] or {}
    ov = sc.get("overall")
    return {"task_id": e["run"].get("task_id"), "prompt_id": e["run"]["prompt_id"], "status": e["run"]["status"],
            "started_at": e["run"].get("started_at") if isinstance(e["run"].get("started_at"), str) else "",
            "model": stats.model_key(e["run"]), "spent": sum(stats.costs(e)),
            "overall": float(ov) if isinstance(ov, (int, float)) and not isinstance(ov, bool) else None,
            "rubric_sha256": sc.get("rubric_sha256") if isinstance(sc.get("rubric_sha256"), str) else None}


def board_row(ws: Workspace, d: Path) -> dict:
    """{run_id, card, summary}: what the board needs of one run (the laptop reads it now; the cloud stores it)."""
    card = card_for(ws, d)
    if not card.get("unreadable"):
        card = {**card, **card_extras(ws, d.name)}
    return {"run_id": d.name, "card": card, "summary": summary_for(d)}
