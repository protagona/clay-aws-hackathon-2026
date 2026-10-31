"""Runs: the board (cards, columns, KPIs), one run's detail, starting a run or its scoring, comparing two runs, and
the rubric copy that scored a run. Run files are untrusted; every read goes through foundry.data.runs."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from foundry.config import ConfigError
from foundry.contract import evaluation_interrupted
from foundry.data import locks as lock
from foundry.data import runs as data_runs
from foundry.data.runs import new_run_id
from foundry.services import configs, jobs, rubrics, stats
from foundry.services.errors import ServiceError
from foundry.services.run_view import monitor
from foundry.services.workspace import Workspace


# --- one run ---------------------------------------------------------------------------------------------------


def run_path(ws: Workspace, run_id: str) -> Path | None:
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
    scores = data_runs.read_scores(path)
    interrupted = scores is not None and evaluation_interrupted(path)
    report_md = data_runs.read_report(path) if scores is not None and not interrupted else None
    steps = data_runs.read_steps_safely(path)
    evaluating = lock.is_locked(path)
    live = evaluating or run.get("status") == "running"
    return {"run": run, "scores": None if interrupted else scores, "report_md": report_md,
            "rubric_info": None if interrupted else rubrics.rubric_info(ws, run, scores),
            "interrupted": interrupted, "evaluating": evaluating, "m": monitor(run, steps, live=live, seen=seen)}


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
    with ws.lock:
        active = jobs.alive_of(ws, "run")
        if active is not None:
            raise ServiceError(409, f"A run is already in progress: {active.run_id}")
        run_id = new_run_id(prompt_id)
        jobs.spawn(ws, "run", run_id, f"Run {prompt_id}",
                   ["run", "--config", f"config/{config}.toml", "--prompt", prompt_id,
                    "--runs-dir", str(ws.runs_dir), "--run-id", run_id])
    return run_id


def start_evaluate(ws: Workspace, run_id: str) -> None:
    path = need_run(ws, run_id)
    run = data_runs.read_run(path)
    if run.get("status") == "running":
        raise ServiceError(409, f"Run {run_id} is still running. If it crashed, score it from the CLI: "
                                f"foundry evaluate runs/{run_id} --force.")
    try:
        token = lock.acquire(path, owner="ui")
    except lock.LockHeld as exc:
        raise ServiceError(409, str(exc)) from None
    config = configs.eval_config_for(ws, run)
    with ws.lock:
        jobs.spawn(ws, "evaluate", run_id, f"Evaluation of {run_id}",
                   ["evaluate", str(path), "--config", config, "--lock-held", token], lock_token=token, lock_dir=path)


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


def short(score) -> str:
    return "—" if score is None else f"{score:.2f}".removeprefix("0")


def when(iso: str | None) -> str:
    try:
        return datetime.strptime(iso.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z").strftime("%m-%d %H:%M")
    except (AttributeError, ValueError):
        return ""


def cards(ws: Workspace) -> list[dict]:
    out = []
    for d in data_runs.run_dirs(ws.runs_dir):
        run = data_runs.read_run(d)
        if run is None:
            out.append({"unreadable": True, "name": d.name, "started_at": ""})
            continue
        scores = data_runs.read_scores(d)
        status = run["status"]
        if lock.is_locked(d):
            headline = "evaluating"
        elif status == "running":
            headline = "running"
        elif scores is None:
            headline = "not evaluated" if not data_runs.has_scores_file(d) else "incomplete"
        elif evaluation_interrupted(d) or scores["overall"] is None:
            headline = "incomplete"
        else:
            headline = f"{scores['overall']:.2f}"
        sub = ""
        if scores is not None and headline not in ("incomplete", "evaluating") and scores["workflow"]["score"] is not None:
            sub = f"W{short(scores['workflow']['score'])} O{short(scores['output']['score'])}"
        started = run.get("started_at") if isinstance(run.get("started_at"), str) else ""
        out.append({"unreadable": False, "run_id": d.name, "prompt_id": run["prompt_id"],
                    "model": stats.model_key(run), "headline": headline, "sub": sub, "status": status,
                    "when": when(started), "footer": f"{run.get('task_id', '?')} · …{d.name[-4:]}",
                    "started_at": started})
    return sorted(out, key=lambda c: c["started_at"], reverse=True)


def card_extras(ws: Workspace, run_id: str) -> dict:
    """Numbers a board card shows beyond the card basics (all from the run's own files)."""
    path = ws.runs_dir / run_id
    sc = data_runs.read_scores(path) or {}
    run = data_runs.read_run(path) or {}
    agents = run.get("agents") if isinstance(run.get("agents"), dict) else {}
    cost = None
    wf = sc.get("workflow") if isinstance(sc.get("workflow"), dict) else {}
    if isinstance(wf.get("cost_usd"), (int, float)):
        cost = wf["cost_usd"]
    mode = agents.get("mode") if isinstance(agents.get("mode"), str) else None
    started: list[str] = []
    live_pipeline = mode == "pipeline" and run.get("status") == "running"
    if live_pipeline or cost is None:  # unscored runs: cost from the trace; live pipeline runs: agents started so far
        total, seen_cost = 0.0, False
        for s in data_runs.read_trace_lenient(path):
            if live_pipeline and s.get("type") == "agent_step" and isinstance(s.get("name"), str):
                started.append(s["name"])
            c = s.get("cost_usd")
            if isinstance(c, (int, float)) and not isinstance(c, bool):
                total, seen_cost = total + c, True
        if cost is None and seen_cost:
            cost = round(total, 6)
    return {"overall": sc.get("overall"), "workflow": wf.get("score"),
            "output": (sc.get("output") or {}).get("score") if isinstance(sc.get("output"), dict) else None,
            "cost": cost, "agents_mode": mode, "agents_started": started[:8]}


def entries(ws: Workspace) -> list[dict]:
    """Every readable run with its scores (None if never or partly scored) and steps: the dashboard's input."""
    out = []
    for d in data_runs.run_dirs(ws.runs_dir):
        run = data_runs.read_run(d)
        if run is None:
            continue
        scores = data_runs.read_scores(d)
        if scores is not None and evaluation_interrupted(d):
            scores = None
        out.append({"run_id": d.name, "run": run, "scores": scores, "steps": data_runs.read_steps_safely(d)})
    return out


def board_kpis(ws: Workspace, all_entries: list[dict] | None = None) -> dict:
    """Runs-board KPIs (increment 9, A5): spend over every run and evaluation; mean over the active rubric only."""
    all_entries = all_entries if all_entries is not None else entries(ws)
    _, active = rubrics.rubric_options(ws, all_entries)
    every = stats.build(all_entries)["kpis"]
    scored = stats.build([e for e in all_entries if (e["scores"] or {}).get("rubric_sha256") in active])["kpis"]
    # Each run's share (increment 11), so the board can total just the runs its filters show.
    shares = {}
    for e in all_entries:
        ov = (e["scores"] or {}).get("overall") if (e["scores"] or {}).get("rubric_sha256") in active else None
        shares[e["run_id"]] = {"spent": sum(stats.costs(e)),
                               "overall": float(ov) if isinstance(ov, (int, float)) and not isinstance(ov, bool) else None}
    return {"spent": every["spent"], "mean": scored["mean"], "scored": scored["scored"], "shares": shares}


def board(ws: Workspace) -> dict:
    """GET /api/runs: every card in its column, with its KPI share, plus the board's KPIs."""
    k = board_kpis(ws)
    out = []
    for c in cards(ws):
        if c["unreadable"]:
            out.append({"run_id": c["name"], "unreadable": True, "column": "attention", "prompt_id": "?",
                        "model": "unreadable run.json", "status": "?", "headline": "unreadable",
                        "kpi": {"spent": 0.0, "overall": None}})
            continue
        out.append({**c, "column": board_column(c), **card_extras(ws, c["run_id"]),
                    "kpi": k["shares"].get(c["run_id"], {"spent": 0.0, "overall": None})})
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
