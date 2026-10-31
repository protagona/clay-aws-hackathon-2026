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


class Batch:
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

    # --- resume ----------------------------------------------------------------------------------------

    def _resolve(self, job: Job) -> Path | None:
        """The attempt to use: the latest finished one, or None if a (new) attempt must run."""
        attempts = [self.runs / job.run_id] + sorted(self.runs.glob(f"{job.run_id}-retry*"),
                                                     key=lambda p: int(p.name.rsplit("retry", 1)[1]))
        job.attempts = [a for a in attempts if (a / "run.json").exists()]
        if job.attempts and _finished(job.attempts[-1]):
            return job.attempts[-1]
        return None

    def _next_id(self, job: Job) -> str:
        if not job.attempts and not (self.runs / job.run_id).exists():
            return job.run_id
        n = 1
        while (self.runs / f"{job.run_id}-retry{n}").exists():
            n += 1
        return f"{job.run_id}-retry{n}"

    # --- execution -------------------------------------------------------------------------------------

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
        with open(self.logs / log_name, "w") as log:
            return subprocess.run([sys.executable, "-m", "foundry", *args], cwd=project_root(),
                                  stdout=log, stderr=subprocess.STDOUT).returncode

    def _work(self, job: Job) -> None:
        existing = self._resolve(job)
        if existing is None:
            with self.lock:
                if self.max_cost is not None and self.spent() >= self.max_cost:
                    job.status = "skipped (budget)"
                    self._report(job)
                    return
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                run_id = self._next_id(job)
            try:
                code = self._exec(["run", "--config", str(job.variant.config), "--prompt", job.prompt,
                                   "--runs-dir", str(self.runs), "--run-id", run_id], f"{run_id}.run.log")
            finally:
                with self.lock:
                    self.active -= 1
            if code == 2 or not (self.runs / run_id / "run.json").exists():
                job.status = "error"
                self._report(job)
                return
            existing = self.runs / run_id
        job.run_dir = existing
        if self.evaluate and not _evaluated(existing):
            self._exec(["evaluate", str(existing), "--config", self.eval_config], f"{existing.name}.eval.log")
        job.status = "ran"
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
        meta = {**(meta or {}), "jobs": len(self.jobs), "runs_dir": str(self.runs), "status": "running",
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        _write_json(self.out / "batch.json", meta)
        write_summary(self.jobs, self.out)
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            list(pool.map(self._work, self.jobs))
        bad = [j for j in self.jobs if j.status in ("error", "skipped (budget)")]
        code = 1 if bad else 0
        _write_json(self.out / "batch.json", {**meta, "status": "finished", "exit_code": code,
                                              "spent_usd": round(self.spent(), 6),
                                              "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        self.log(f"summary: {self.out / 'summary.md'}  (spent ${self.spent():.3f}"
                 + (f" of ${self.max_cost:.2f}" if self.max_cost is not None else "") + ")")
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
    for k in ("output", "overall"):
        row[k] = "" if row[k] is None else row[k]
    return row


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
    rows = [summary_row(j) for j in jobs]
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
        costs = [r["cost_usd"] for r in ran if isinstance(r["cost_usd"], float)]
        calls = [r["model_calls"] for r in ran if r["model_calls"] != ""]
        mean = lambda v, f: f.format(statistics.mean(v)) if v else "n/a"  # noqa: E731
        sp = {k: "—" if v is None else f"{v:.2f}" for k, v in spread(overall).items()}
        lines.append(f"| {name} | {rs[0]['model']} | {ok}/{len(rs)} | {sp['mean']} | {sp['min']} | {sp['max']} | "
                     f"{sp['stdev']} | {mean(costs, '${:.3f}')} | {mean(calls, '{:.1f}')} |")
    lines += ["", "## Jobs", "", "| " + " | ".join(SUMMARY_FIELDS[:-1]) + " |",
              "|" + "---|" * (len(SUMMARY_FIELDS) - 1)]
    for r in rows:
        cells = [f"{r[k]:.4f}" if k == "cost_usd" and isinstance(r[k], float) else str(r[k]) for k in SUMMARY_FIELDS[:-1]]
        lines.append("| " + " | ".join(cells) + " |")
    tmp = out / "summary.md.tmp"
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(out / "summary.md")


def main(args) -> int:
    out = Path(args.out)
    try:
        models = [m.strip() for m in (args.models or "").split(",") if m.strip()]
        prefix = getattr(args, "run_prefix", None) or ""
        if prefix and not re.fullmatch(r"[A-Za-z0-9._-]+", prefix):
            raise BatchError("--run-prefix may only contain letters, digits, '.', '_' and '-'")
        jobs = plan(args.config, models, args.prompts, args.repeats, out, prefix)
        eval_config = args.eval_config or str(jobs[0].variant.config)
        if args.evaluate:
            load_eval_config(eval_config)
        batch = Batch(jobs, out, args.concurrency, args.evaluate, eval_config, args.max_cost,
                      runs_dir=Path(args.runs_dir) if getattr(args, "runs_dir", None) else None)
    except (BatchError, ConfigError) as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"batch: {len(jobs)} jobs ({len({j.variant.name for j in jobs})} variants), concurrency "
          f"{args.concurrency}" + (f", budget ${args.max_cost:.2f}" if args.max_cost is not None else "") + f" -> {out}")
    meta = {"id": out.name, "configs": args.config, "models": models, "prompts": args.prompts, "repeats": args.repeats,
            "concurrency": args.concurrency, "evaluate": args.evaluate, "max_cost": args.max_cost}
    return batch.run(meta)
