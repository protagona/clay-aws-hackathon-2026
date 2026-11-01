"""Runs: the board (cards, columns, KPIs), one run's detail, starting a run or its scoring, comparing two runs, and
the rubric copy that scored a run. Run files are untrusted; every read goes through foundry.data.runs."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from foundry.config import ConfigError
from foundry.contract import evaluation_interrupted
from foundry.data import project as data_project
from foundry.data import runs as data_runs
from foundry.data.runs import new_run_id
from foundry.services import cards, configs, jobs, rubrics, spend, state, stats
from foundry.services.cards import (  # noqa: F401 (re-exported: callers and tests use runs.*)
    INTERRUPTED_REASON, card_extras, card_for, entry_for, failure_reason, short, summary_for, when)
from foundry.services.errors import ServiceError
from foundry.services.run_view import monitor
from foundry.services.workspace import Workspace


# --- one run ---------------------------------------------------------------------------------------------------


def run_path(ws: Workspace, run_id: str) -> Path | None:
    if data_runs.is_valid_run_id(run_id) and not run_id.startswith("."):
        state.of(ws).fresh_run(ws, run_id)
    return data_runs.safe_run_path(ws.runs_dir, run_id)


def need_run(ws: Workspace, run_id: str) -> Path:
    path = run_path(ws, run_id)
    if path is None:
        raise ServiceError(404, "Run not found")
    return path


def detail(ws: Workspace, path: Path, seen: int | None = None) -> dict:
    """Everything the run drawer shows, from the run's own files. The report is Markdown (`report_md`); the API
    renders it, and model text, to safe HTML."""
    run = {**data_runs.read_run(path), "run_id": path.name}  # links use the directory name, never run.json's
    if interrupted(ws, path.name, run.get("status")):  # its job ended; run.json will never say so
        run["status"] = "interrupted"
    scores = data_runs.read_scores(path)
    scoring_cut = scores is not None and evaluation_interrupted(path)
    report_md = data_runs.read_report(path) if scores is not None and not scoring_cut else None
    steps = data_runs.read_steps_safely(path)
    evaluating = jobs.is_evaluating(ws, path)
    live = evaluating or run.get("status") == "running"
    return {"run": run, "scores": None if scoring_cut else scores, "report_md": report_md,
            "rubric_info": None if scoring_cut else rubrics.rubric_info(ws, run, scores),
            "interrupted": scoring_cut, "evaluating": evaluating, "m": monitor(run, steps, live=live, seen=seen)}


def overall(ws: Workspace, run_id: str) -> float | None:
    v = (data_runs.read_scores(ws.runs_dir / run_id) or {}).get("overall")
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def run_rubric(ws: Workspace, run_id: str) -> dict:
    """Increment 10, R2: the exact rubric copied into a run when it was evaluated (untrusted: regular file only)."""
    path = need_run(ws, run_id)
    copy = path / "rubric.json"
    if not data_runs.is_regular(copy):
        raise ServiceError(404, "This run has no rubric copy (not scored yet)")
    task = (data_runs.read_run(path) or {}).get("task_id")
    store = rubrics.store_for(ws, task if isinstance(task, str) and task in configs.task_ids(ws) else "adb")
    return rubrics.with_meta(store, copy)


# --- starting work -------------------------------------------------------------------------------------------------


def start_run(ws: Workspace, prompt_id: str, config: str) -> str:
    configs.require_config(ws, config)
    try:
        cfg = configs.load(ws, config)
        prompts = configs.task_prompts(ws, cfg.task.id)
    except ConfigError as exc:
        raise ServiceError(400, configs.relative_paths(ws, str(exc))) from None
    configs.check_runnable(ws, cfg, config, [prompt_id], prompts)
    run_id = new_run_id(prompt_id)
    reservation = spend.run_reservation(cfg, config) if ws.cap_usd is not None else 0.0
    jobs.start(ws, "run", run_id, f"Run {prompt_id}",
               ["run", "--config", f"config/{config}.toml", "--prompt", prompt_id, "--runs-dir", str(ws.runs_dir),
                "--run-id", run_id], singleton=True, prompt_ids=[prompt_id],
               task=cfg.task.id, reservation=reservation)
    return run_id


def start_evaluate(ws: Workspace, run_id: str) -> None:
    path = need_run(ws, run_id)
    run = data_runs.read_run(path)
    if run.get("status") == "running":
        raise ServiceError(409, f"Run {run_id} is still running. If it crashed, score it from the CLI: "
                                f"foundry evaluate runs/{run_id} --force.")
    config = configs.eval_config_for(ws, run)
    reservation = 0.0
    if ws.cap_usd is not None:
        stem = config.removeprefix("config/").removesuffix(".toml")
        try:
            reservation = spend.eval_reservation(configs.load(ws, stem), stem)
        except ConfigError as exc:
            raise ServiceError(400, configs.relative_paths(ws, str(exc))) from None
    jobs.start(ws, "evaluate", run_id, f"Evaluation of {run_id}", ["evaluate", str(path), "--config", config],
               evaluate_dir=path, reservation=reservation)


# --- the board -------------------------------------------------------------------------------------------------


def board_column(card: dict) -> str:
    """Which live-board column a run card belongs in (increment 9, U2; computed here so the UI stays thin)."""
    h = card.get("headline")
    if h == "running":
        return "running"
    if h == "evaluating":
        return "evaluating"
    if card.get("status") == "succeeded" and h not in ("not evaluated", "incomplete"):
        return "scored"
    return "attention"


def interrupted(ws: Workspace, run_id: str, status, cards_fresh: bool | None = None, job=None) -> bool:
    """A run that says running but whose run job ended without saying so (backend-parity ADR-2). The laptop's card is
    read from run.json now, so a dead job means a crash; a stored cloud card can lag a normal finish, so there the job
    must say it was interrupted. A run with no job (the CLI, a batch, the receiver) is never overlaid."""
    if status != "running":
        return False
    job = job if job is not None else jobs.of_run(ws, "run", run_id)
    if job is None or job.alive:
        return False
    fresh = state.of(ws).cards_fresh if cards_fresh is None else cards_fresh
    return fresh or getattr(job, "status", None) == "interrupted"


def entries(ws: Workspace) -> list[dict]:
    """Every readable run with its scores and steps: the dashboard's input."""
    state.of(ws).fresh_runs(ws)
    return [e for e in (cards.entry_for(d) for d in data_runs.run_dirs(ws.runs_dir)) if e is not None]


def active_rubrics(ws: Workspace) -> set[str]:
    """The sha256 of every task's active rubric.json: the board's mean counts runs scored by these only."""
    state.of(ws).fresh_projects(ws)
    return {sha for task in configs.task_ids(ws) if (sha := data_project.active_rubric_sha(ws.root, task))}


def kpi_shares(rows: list[dict], active: set[str]) -> dict:
    """Spend over every run and its scoring; mean and count over runs scored by an active rubric; each run's share
    (increment 11: the board totals just the runs its filters show). From the rows' summaries alone."""
    spent, scored, shares = 0.0, [], {}
    for row in rows:
        s = row.get("summary") or {}
        ov = s.get("overall") if s.get("rubric_sha256") in active else None
        spent += s.get("spent") or 0.0
        if ov is not None:
            scored.append(ov)
        shares[row["run_id"]] = {"spent": s.get("spent") or 0.0, "overall": ov}
    return {"spent": spent, "mean": sum(scored) / len(scored) if scored else None, "scored": len(scored),
            "shares": shares}


def shares(ws: Workspace) -> dict:
    """Each run's KPI share (spend, and overall when its rubric is active): the prompt pages' scores and costs."""
    return kpi_shares(state.of(ws).board_rows(ws), active_rubrics(ws))["shares"]


UNREADABLE = {"unreadable": True, "column": "attention", "prompt_id": "?", "model": "unreadable run.json", "status": "?",
              "headline": "unreadable", "kpi": {"spent": 0.0, "overall": None}}


def board(ws: Workspace) -> dict:
    """GET /api/runs: every card in its column, with its KPI share, plus the board's KPIs. The same assembly on a
    laptop and in AWS (backend-parity ADR-2): rows from the state, live state overlaid here."""
    st = state.of(ws)
    run_jobs = {j.run_id: j for j in jobs.of_kind(ws, "run")}  # before the rows: a run finishing in between isn't
    rows = st.board_rows(ws)                                    # mistaken for a crash
    evaluating = st.evaluating_runs(ws)
    k = kpi_shares(rows, active_rubrics(ws))
    out = []
    order = lambda r: ((r.get("card") or {}).get("started_at") or "", r["run_id"])  # noqa: E731 (ties: one order everywhere)
    for row in sorted(rows, key=order, reverse=True):
        card = row.get("card")
        if not card or card.get("unreadable"):
            out.append({"run_id": row["run_id"], **UNREADABLE})
            continue
        c = {f: card[f] for f in cards.CARD_FIELDS if f in card}  # a stored card from other code: known fields only
        if row["run_id"] in evaluating:
            c["headline"], c["sub"] = "evaluating", ""
        elif interrupted(ws, row["run_id"], c.get("status"), st.cards_fresh, run_jobs.get(row["run_id"])):
            c["headline"] = c["status"] = "interrupted"
            c["reason"] = cards.INTERRUPTED_REASON
        out.append({**c, "run_id": row["run_id"], "column": board_column(c),
                    "kpi": k["shares"].get(row["run_id"], {"spent": 0.0, "overall": None})})
    live = sum(c["column"] in ("running", "evaluating") for c in out)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    return {"runs": out, "live": live + (1 if ws.receiver is not None else 0),
            "kpis": {"runs": len(out), "today": sum(str(c.get("started_at", "")).startswith(today) for c in out),
                     "mean": k["mean"], "scored": k["scored"], "spent": k["spent"],
                     "live": live, "running": sum(c["column"] == "running" for c in out),
                     "evaluating": sum(c["column"] == "evaluating" for c in out)}}


# --- compare ---------------------------------------------------------------------------------------------------


def compare(ws: Workspace, a: str, b: str) -> tuple[dict, dict, list[dict]]:
    pa, pb = run_path(ws, a), run_path(ws, b)
    if pa is None or pb is None:
        raise ServiceError(404, "Run not found")
    sa, sb = compare_side(pa), compare_side(pb)
    return sa, sb, compare_rows(sa, sb)


def compare_side(path: Path) -> dict:
    """One run's numbers for the compare page (F-3), from its own files; scores are None until evaluated."""
    run = {**data_runs.read_run(path), "run_id": path.name}
    scores = data_runs.read_scores(path)
    if scores is not None and evaluation_interrupted(path):
        scores = None
    steps = data_runs.read_steps_safely(path)
    tools = [s for s in steps if s.type == "tool_call"]
    costs = [s.cost_usd for s in steps if s.cost_usd is not None]
    duration = None
    try:
        fmt = "%Y-%m-%dT%H:%M:%S.%f%z"
        duration = (datetime.strptime(run["ended_at"].replace("Z", "+0000"), fmt)
                    - datetime.strptime(run["started_at"].replace("Z", "+0000"), fmt)).total_seconds()
    except (KeyError, AttributeError, TypeError, ValueError):
        pass
    markers = {s.step: s.name for s in steps if s.type == "agent_step"}
    return {"run": run, "scores": scores, "steps": steps, "cost": sum(costs) if costs else None,
            "label": stats.model_key(run), "agent_of": {s.step: markers[s.parent] for s in steps if s.parent in markers},
            "model_calls": sum(s.type == "llm_call" for s in steps), "tool_calls": len(tools),
            "tool_errors": sum(s.status == "error" for s in tools), "duration": duration}


def signed(d: float, fmt: str) -> str:
    return "0" if abs(d) < 1e-9 else ("+" if d > 0 else "−") + fmt.format(abs(d))


def compare_rows(a: dict, b: dict) -> list[dict]:
    """Rows of the compare table: label, A, B, and B - A, from untrusted files (every number is type-checked)."""
    num = data_runs.num
    rows = []

    def add(label, va, vb, fmt="{:.2f}", indent=False, show=None, better="higher"):
        show = show or (lambda v: fmt.format(v))
        delta = signed(vb - va, fmt) if va is not None and vb is not None else ""
        rows.append({"label": label, "a": "—" if va is None else show(va), "b": "—" if vb is None else show(vb),
                     "delta": delta, "indent": indent, "better": better})  # which way is an improvement (increment 12)

    def sc(side, *keys):
        v = side["scores"]
        for k in keys:
            v = v.get(k) if isinstance(v, dict) else None
        return num(v)

    def criteria(side) -> dict:
        out = {}
        crit = ((side["scores"] or {}).get("output") or {}).get("criteria")
        for c in crit if isinstance(crit, list) else []:
            if isinstance(c, dict) and isinstance(c.get("id"), str) and num(c.get("score")) is not None:
                out[c["id"]] = (num(c["score"]), num(c.get("max")))
        return out

    def components(side) -> dict:
        comp = ((side["scores"] or {}).get("workflow") or {}).get("components")
        return {k: num(v) for k, v in comp.items() if isinstance(k, str)} if isinstance(comp, dict) else {}

    add("Overall", sc(a, "overall"), sc(b, "overall"))
    add("Workflow", sc(a, "workflow", "score"), sc(b, "workflow", "score"))
    ca, cb = components(a), components(b)
    for k in dict.fromkeys([*ca, *cb]):
        add(k, ca.get(k), cb.get(k), indent=True)
    add("Output", sc(a, "output", "score"), sc(b, "output", "score"))
    ka, kb = criteria(a), criteria(b)
    for k in dict.fromkeys([*ka, *kb]):
        va, vb = ka.get(k), kb.get(k)

        def frac(v):
            return f"{v[0]:g}/{v[1]:g}" if v[1] is not None else f"{v[0]:g}"
        rows.append({"label": k, "a": frac(va) if va else "—", "b": frac(vb) if vb else "—", "indent": True,
                     "delta": signed(vb[0] - va[0], "{:g}") if va and vb else "", "better": "higher"})
    add("Cost", a["cost"], b["cost"], "${:.3f}", better="lower")
    add("Model calls", a["model_calls"], b["model_calls"], "{:g}", better=None)
    add("Tool calls (failed)", a["tool_calls"], b["tool_calls"], "{:g}", better=None)
    rows[-1]["a"] = f"{a['tool_calls']} ({a['tool_errors']})"
    rows[-1]["b"] = f"{b['tool_calls']} ({b['tool_errors']})"
    add("Duration", a["duration"], b["duration"], "{:.0f} s", better="lower")
    return rows
