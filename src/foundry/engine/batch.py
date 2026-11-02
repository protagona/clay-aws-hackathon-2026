"""`foundry batch`: run prompts x variants x repeats as real `foundry run` subprocesses, with a concurrency cap,
a budget stop, resume, and a summary table.

A variant is a config file. With --models, variants are derived from one base config by changing only
[model] id; the derived files are written to <out>/configs/ so every run's config is on disk (provenance).
"""

from __future__ import annotations

import csv
import json
import re
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from foundry.config import ConfigError, load_config, load_eval_config, project_root
from foundry.contract import evaluation_interrupted
from foundry.data.tasks import UnknownPrompt, load_prompts, resolve_prompt

SUMMARY_FIELDS = ["variant", "model", "prompt", "repeat", "status", "model_calls", "tool_calls", "tool_errors",
                  "tokens_in", "tokens_out", "cost_usd", "workflow", "output", "overall", "duration_s", "run_dir"]


class BatchError(Exception):
    """A usage problem; nothing was run (exit 2)."""


@dataclass
class Variant:
    name: str
    config: Path
    model: str


@dataclass
class Job:
    variant: Variant
    prompt: str
    repeat: int
    prefix: str = ""
    status: str = "pending"  # pending | ran | error | skipped (budget)
    run_dir: Path | None = None
    attempts: list[Path] = field(default_factory=list)

    @property
    def run_id(self) -> str:
        return f"{self.prefix}{self.variant.name}_{self.prompt}_r{self.repeat}"


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "-", text).strip("-")[:60]


def derive_model_configs(base: Path, models: list[str], out: Path) -> list[Variant]:
    text = base.read_text()
    section = re.search(r"^\[model\][^\n]*\n(?:(?!^\[).*\n?)*", text, flags=re.M)
    if section is None or not re.search(r'^id = ".*"$', section.group(0), flags=re.M):
        raise BatchError(f"{base}: no [model] id to override for --models")
    (out / "configs").mkdir(parents=True, exist_ok=True)
    variants = []
    for model in models:
        new_section = re.sub(r'^id = ".*"$', f'id = "{model}"', section.group(0), count=1, flags=re.M)
        path = out / "configs" / f"{slug(model)}.toml"
        path.write_text(f"# Derived by foundry batch from {base}: only [model] id differs.\n"
                        + text.replace(section.group(0), new_section))
        variants.append(Variant(slug(model), path, model))
    return variants


def plan(configs: list[str], models: list[str], prompts: str, repeats: int, out: Path, prefix: str = "") -> list[Job]:
    if not configs:
        raise BatchError("at least one --config is required")
    if repeats < 1:
        raise BatchError("--repeats must be >= 1")
    if models and len(configs) != 1:
        raise BatchError("--models derives variants from exactly one --config")
    try:
        if models:
            variants = derive_model_configs(Path(configs[0]), models, out)
        else:
            variants = [Variant(slug(Path(c).stem), Path(c), load_config(c).model.id) for c in configs]
        if len({v.name for v in variants}) != len(variants):
            raise BatchError("variant names collide; give the configs distinct file names")
        cfgs = {v.name: load_config(v.config) for v in variants}
        task = {c.task.id for c in cfgs.values()}
        if len(task) != 1:
            raise BatchError(f"all variants must share one task (got {sorted(task)})")
        (task_id,) = task
        ids = [p.prompt_id for p in load_prompts(task_id)] if prompts == "all" else [p.strip() for p in prompts.split(",") if p.strip()]
        for pid in ids:
            resolve_prompt(task_id, pid)
    except (ConfigError, UnknownPrompt) as exc:
        raise BatchError(str(exc)) from None
    return [Job(v, p, r, prefix) for v in variants for p in ids for r in range(1, repeats + 1)]


def run_cost(run_dir: Path) -> float:
    total = 0.0
    trace = run_dir / "trace.jsonl"
    if trace.exists():
        for line in trace.read_text().splitlines():
            try:
                total += json.loads(line).get("cost_usd") or 0.0
            except ValueError:
                continue
    scores = _load(run_dir / "scores.json")
    if scores:
        total += (scores.get("evaluation") or {}).get("cost_usd") or 0.0
    return total


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _finished(run_dir: Path) -> bool:
    run = _load(run_dir / "run.json")
    return bool(run) and run.get("status") != "running"


def _evaluated(run_dir: Path) -> bool:
    return (run_dir / "scores.json").exists() and not evaluation_interrupted(run_dir)


@dataclass
class ItemContext:
    """What one batch item needs from its driver (backend-parity ADR-5): the laptop's `Batch` and the cloud's
    `workers.batch_item` each supply their own way to run a `foundry` command and to read the spend so far."""
    runs: Path
    evaluate: bool
    eval_config: str
    max_cost: float | None
    exec: object  # (args: list[str], log_name: str) -> exit code
    spent: object  # () -> USD spent by this batch so far
    lock: object = field(default_factory=threading.RLock)  # around the budget check and the attempt's name
    resume: bool = True  # reuse a finished attempt in the runs dir, or name a new one -retryN (a laptop re-run)


def _resolve(job: Job, runs: Path) -> Path | None:
    """The attempt to use: the latest finished one, or None if a (new) attempt must run."""
    attempts = [runs / job.run_id] + sorted(runs.glob(f"{job.run_id}-retry*"),
                                            key=lambda p: int(p.name.rsplit("retry", 1)[1]))
    job.attempts = [a for a in attempts if (a / "run.json").exists()]
    if job.attempts and _finished(job.attempts[-1]):
        return job.attempts[-1]
    return None


def _next_id(job: Job, runs: Path) -> str:
    if not job.attempts and not (runs / job.run_id).exists():
        return job.run_id
    n = 1
    while (runs / f"{job.run_id}-retry{n}").exists():
        n += 1
    return f"{job.run_id}-retry{n}"


def run_item(job: Job, ctx: ItemContext) -> str:
    """One job of a batch, the same on a laptop and in the cloud: skip it once the budget is spent, run it (resuming
    a finished attempt when `ctx.resume`), score it unless it's scored already. Sets `job.status` and `job.run_dir`
    and returns the status: "ran", "error" or "skipped (budget)"."""
    existing = _resolve(job, ctx.runs) if ctx.resume else None
    if existing is None:
        with ctx.lock:
            if ctx.max_cost is not None and ctx.spent() >= ctx.max_cost:
                job.status = "skipped (budget)"
                return job.status
            run_id = _next_id(job, ctx.runs) if ctx.resume else job.run_id
        code = ctx.exec(["run", "--config", str(job.variant.config), "--prompt", job.prompt,
                         "--runs-dir", str(ctx.runs), "--run-id", run_id], f"{run_id}.run.log")
        if code == 2 or not (ctx.runs / run_id / "run.json").exists():
            job.status = "error"
            return job.status
        existing = ctx.runs / run_id
    job.run_dir = existing
    if ctx.evaluate and not _evaluated(existing):
        ctx.exec(["evaluate", str(existing), "--config", ctx.eval_config], f"{existing.name}.eval.log")
    job.status = "ran"
    return job.status


class Batch:
    """The laptop's driver: every item in a thread pool of `concurrency`, `foundry` commands as subprocesses with
    their output in <out>/logs/, the spend read from the batch's own runs."""

    def __init__(self, jobs: list[Job], out: Path, concurrency: int, evaluate: bool, eval_config: str,
                 max_cost: float | None, log=print, runs_dir: Path | None = None):
        if concurrency < 1:
            raise BatchError("--concurrency must be >= 1")
        self.jobs, self.out, self.concurrency = jobs, out, concurrency
        self.evaluate, self.eval_config, self.max_cost, self.log = evaluate, eval_config, max_cost, log
        self.runs = Path(runs_dir) if runs_dir else out / "runs"  # the UI shares its runs dir with batches
        self.logs = out / "logs"
        self.lock = threading.RLock()  # _report runs both inside and outside the budget check's lock
        self.active = 0
        self.max_active = 0
        self.done = 0
        self.ctx = ItemContext(self.runs, evaluate, eval_config, max_cost, self._exec, self.spent, self.lock)

    def own_runs(self) -> list[Path]:
        """This batch's run dirs (all attempts), matched exactly: the runs dir may be shared."""
        if not self.runs.exists():
            return []
        ids = {j.run_id for j in self.jobs}
        return [d for d in self.runs.iterdir() if d.is_dir()
                and (d.name in ids or d.name.split("-retry")[0] in ids)]

    def spent(self) -> float:
        return sum(run_cost(d) for d in self.own_runs())

    def _exec(self, args: list[str], log_name: str) -> int:
        counted = args[0] == "run"
        if counted:
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
        try:
            with open(self.logs / log_name, "w") as log:
                return subprocess.run([sys.executable, "-m", "foundry", *args], cwd=project_root(),
                                      stdout=log, stderr=subprocess.STDOUT).returncode
        finally:
            if counted:
                with self.lock:
                    self.active -= 1

    def _work(self, job: Job) -> None:
        run_item(job, self.ctx)
        self._report(job)

    def _report(self, job: Job) -> None:
        with self.lock:
            self.done += 1
            row = summary_row(job)
            cost = f" ${row['cost_usd']:.3f}" if isinstance(row["cost_usd"], float) else ""
            score = f" overall {row['overall']}" if row["overall"] not in (None, "") else ""
            self.log(f"[{self.done}/{len(self.jobs)}] {job.variant.name} {job.prompt} r{job.repeat}: "
                     f"{row['status']}{score}{cost}")
            write_summary(self.jobs, self.out)

    def run(self, meta: dict | None = None) -> int:
        self.runs.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        meta = start_meta(meta or {}, self.jobs, self.out, self.runs)
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            list(pool.map(self._work, self.jobs))
        code = finalize(self.out, meta, [summary_row(j) for j in self.jobs], self.spent())
        self.log(f"summary: {self.out / 'summary.md'}  (spent ${self.spent():.3f}"
                 + (f" of ${self.max_cost:.2f}" if self.max_cost is not None else "") + ")")
        return code


def start_meta(meta: dict, jobs: list[Job], out: Path, runs: Path) -> dict:
    """batch.json at the start (status running) and a summary with every row pending."""
    meta = {**meta, "jobs": len(jobs), "runs_dir": str(runs), "status": "running",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "batch.json", meta)
    write_summary(jobs, out)
    return meta


def finalize(out: Path, meta: dict, rows: list[dict], spent: float, extra_bad: bool = False) -> int:
    """batch.json finished, with its exit code: 1 if any job errored or was skipped for the budget (or the driver
    knows of another failure), else 0."""
    code = 1 if extra_bad or any(r["status"] in ("error", "skipped (budget)") for r in rows) else 0
    _write_json(out / "batch.json", {**meta, "status": "finished", "exit_code": code, "spent_usd": round(spent, 6),
                                     "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    return code


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def summary_row(job: Job) -> dict:
    row = {k: "" for k in SUMMARY_FIELDS}
    row.update(variant=job.variant.name, model=job.variant.model, prompt=job.prompt, repeat=job.repeat,
               status=job.status)
    d = job.run_dir
    if job.status != "ran" or d is None:
        return row
    run = _load(d / "run.json") or {}
    steps = []
    for line in (d / "trace.jsonl").read_text().splitlines() if (d / "trace.jsonl").exists() else []:
        try:
            steps.append(json.loads(line))
        except ValueError:
            continue
    llm = [s for s in steps if s.get("type") == "llm_call"]
    tools = [s for s in steps if s.get("type") == "tool_call"]
    scores = _load(d / "scores.json") or {}
    row.update(
        status=run.get("status", "?"), model_calls=len(llm), tool_calls=len(tools),
        tool_errors=sum(s.get("status") == "error" for s in tools),
        tokens_in=sum(s.get("tokens_in") or 0 for s in llm), tokens_out=sum(s.get("tokens_out") or 0 for s in llm),
        cost_usd=round(run_cost(d), 6), run_dir=str(d),
        workflow=(scores.get("workflow") or {}).get("score", ""), output=(scores.get("output") or {}).get("score"),
        overall=scores.get("overall"), duration_s=_duration(run),
    )
    return _typed(row)


INT_FIELDS = ("repeat", "model_calls", "tool_calls", "tool_errors", "tokens_in", "tokens_out")
FLOAT_FIELDS = ("cost_usd", "workflow", "output", "overall", "duration_s")


def _typed(row: dict) -> dict:
    """One summary row with typed values: ints and floats where known, "" where not. A row built from a run and the
    same row read back from summary.csv (rows_from_csv) are equal, so both drivers write the same files."""
    out = {k: row.get(k, "") for k in SUMMARY_FIELDS}
    for k in INT_FIELDS + FLOAT_FIELDS:
        v = out[k]
        try:
            out[k] = "" if v is None or v == "" or isinstance(v, bool) else (int(v) if k in INT_FIELDS else float(v))
        except (TypeError, ValueError):
            out[k] = ""
    for k in SUMMARY_FIELDS:
        if k not in INT_FIELDS + FLOAT_FIELDS:
            out[k] = "" if out[k] is None else str(out[k])
    return out


def rows_from_csv(path: Path) -> list[dict]:
    """A batch's summary.csv as typed rows (empty when there's none)."""
    try:
        with open(path, newline="") as f:
            return [_typed(r) for r in csv.DictReader(f)]
    except OSError:
        return []


def _duration(run: dict) -> float | str:
    from datetime import datetime

    try:
        fmt = "%Y-%m-%dT%H:%M:%S.%f%z"
        a = datetime.strptime(run["started_at"].replace("Z", "+0000"), fmt)
        b = datetime.strptime(run["ended_at"].replace("Z", "+0000"), fmt)
        return round((b - a).total_seconds(), 1)
    except (KeyError, TypeError, ValueError):
        return ""


def spread(values: list[float]) -> dict:
    """Mean, min, max and sample stdev of a variant's scores (D-7); stdev is None below 2 values, all None when empty."""
    if not values:
        return {"mean": None, "min": None, "max": None, "stdev": None}
    return {"mean": statistics.mean(values), "min": min(values), "max": max(values),
            "stdev": statistics.stdev(values) if len(values) > 1 else None}


def write_summary(jobs: list[Job], out: Path) -> None:
    write_rows(out, [summary_row(j) for j in jobs])


def write_rows(out: Path, rows: list[dict]) -> None:
    """summary.csv and summary.md from typed rows, each replaced in one step."""
    tmp = out / "summary.csv.tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(out / "summary.csv")

    lines = ["# Batch summary", "", f"Updated {time.strftime('%Y-%m-%d %H:%M:%S')}. "
             f"{sum(r['status'] not in ('pending',) for r in rows)} of {len(rows)} jobs finished.", "",
             "## Per variant", "", "Overall score across the variant's scored runs (stdev: sample, needs 2 runs).", "",
             "| Variant | Model | Succeeded | Mean | Min | Max | Stdev | Mean cost/run | Mean model calls |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name in dict.fromkeys(r["variant"] for r in rows):
        rs = [r for r in rows if r["variant"] == name]
        ran = [r for r in rs if r["run_dir"]]
        ok = sum(r["status"] == "succeeded" for r in ran)
        overall = [float(r["overall"]) for r in ran if r["overall"] != ""]
        costs = [r["cost_usd"] for r in ran if isinstance(r["cost_usd"], (int, float))]
        calls = [r["model_calls"] for r in ran if r["model_calls"] != ""]
        mean = lambda v, f: f.format(statistics.mean(v)) if v else "n/a"  # noqa: E731
        sp = {k: "—" if v is None else f"{v:.2f}" for k, v in spread(overall).items()}
        lines.append(f"| {name} | {rs[0]['model']} | {ok}/{len(rs)} | {sp['mean']} | {sp['min']} | {sp['max']} | "
                     f"{sp['stdev']} | {mean(costs, '${:.3f}')} | {mean(calls, '{:.1f}')} |")
    lines += ["", "## Jobs", "", "| " + " | ".join(SUMMARY_FIELDS[:-1]) + " |",
              "|" + "---|" * (len(SUMMARY_FIELDS) - 1)]
    for r in rows:
        cells = [f"{r[k]:.4f}" if k == "cost_usd" and isinstance(r[k], (int, float)) else str(r[k]) for k in SUMMARY_FIELDS[:-1]]
        lines.append("| " + " | ".join(cells) + " |")
    tmp = out / "summary.md.tmp"
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(out / "summary.md")


def add_args(parser) -> None:
    """`foundry batch`'s options: the CLI's parser, and the cloud worker reading a batch job's own arguments."""
    parser.add_argument("--config", action="append", required=True, help="a variant config (repeatable)")
    parser.add_argument("--models", help="comma-separated model ids: one variant each, derived from the single --config")
    parser.add_argument("--prompts", required=True, help='comma-separated prompt ids, or "all"')
    parser.add_argument("--repeats", type=int, default=1, help="runs per variant and prompt (default: 1)")
    parser.add_argument("--concurrency", type=int, default=2, help="runs at once (default: 2)")
    parser.add_argument("--evaluate", action="store_true", help="also run foundry evaluate on each run")
    parser.add_argument("--eval-config", help="config for evaluation (default: the first variant's config)")
    parser.add_argument("--max-cost", type=float, help="stop launching runs once recorded cost (USD) reaches this")
    parser.add_argument("--out", required=True, help="batch directory (re-run the same command to resume)")
    parser.add_argument("--runs-dir", help="where run dirs go (default <out>/runs); the UI uses its own runs dir")
    parser.add_argument("--run-prefix", help="prefix for run ids, so batches can share a runs dir")


def spec_from_args(argv: list[str]):
    """A batch job's own command line (with or without its leading "batch") as parsed options."""
    import argparse

    p = argparse.ArgumentParser(prog="foundry batch", add_help=False)
    add_args(p)
    return p.parse_args(argv[1:] if argv[:1] == ["batch"] else argv)


def _shown(config: str) -> str:
    """A config as batch.json records it: relative to the project root when it's inside it (never a server's path)."""
    p, root = Path(config), project_root()
    return p.relative_to(root).as_posix() if p.is_absolute() and p.is_relative_to(root) else config


def prepare(args, out: Path, runs_dir: Path) -> tuple[list[Job], dict, str]:
    """Plan the batch from its options: (jobs, batch.json at the start, the scoring config). Writes batch.json and
    a summary with every row pending. Raises BatchError or ConfigError for a usage problem."""
    models = [m.strip() for m in (args.models or "").split(",") if m.strip()]
    prefix = getattr(args, "run_prefix", None) or ""
    if prefix and not re.fullmatch(r"[A-Za-z0-9._-]+", prefix):
        raise BatchError("--run-prefix may only contain letters, digits, '.', '_' and '-'")
    if args.concurrency < 1:
        raise BatchError("--concurrency must be >= 1")
    jobs = plan(args.config, models, args.prompts, args.repeats, out, prefix)
    eval_config = args.eval_config or str(jobs[0].variant.config)
    if args.evaluate:
        load_eval_config(eval_config)
    meta = {"id": out.name, "configs": [_shown(c) for c in args.config], "models": models, "prompts": args.prompts, "repeats": args.repeats,
            "concurrency": args.concurrency, "evaluate": args.evaluate, "max_cost": args.max_cost}
    return jobs, start_meta(meta, jobs, out, runs_dir), eval_config


def main(args) -> int:
    out = Path(args.out)
    runs_dir = Path(args.runs_dir) if getattr(args, "runs_dir", None) else out / "runs"
    try:
        jobs, meta, eval_config = prepare(args, out, runs_dir)
        batch = Batch(jobs, out, args.concurrency, args.evaluate, eval_config, args.max_cost, runs_dir=runs_dir)
    except (BatchError, ConfigError) as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"batch: {len(jobs)} jobs ({len({j.variant.name for j in jobs})} variants), concurrency "
          f"{args.concurrency}" + (f", budget ${args.max_cost:.2f}" if args.max_cost is not None else "") + f" -> {out}")
    return batch.run(meta)
