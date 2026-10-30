"""Judge sanity check (backlog D-6): does the configured judge rank known-good above known-bad, consistently?

Scores every case in fixtures/judge-cases/ N times with `foundry evaluate`'s own code path (judge + summarizer),
on copies in --out, then checks fixtures/judge-cases/expected.json:

  band         each case's mean output score falls in its band (high / mid / low)
  order        mean output score is strictly decreasing along `order` (strong > short > vague)
  criteria     named criteria never score above a cap (the hallucinated plan's data-fit and feasibility)
  consistency  across repeats, output scores spread by at most max_spread.output and every criterion by at most
               max_spread.criterion points

This proves the judge separates obvious cases and is stable; it does not prove it agrees with humans.

Usage: AWS_PROFILE=<profile> uv run python scripts/judge_sanity.py [--config config/bedrock.toml] [--repeats 3]
           [--cases fixtures/judge-cases] [--out <dir>] [--md results.md]
Exit 0 if every check passes, 1 otherwise. Costs about $0.04 per case and repeat with the Opus 4.6 judge.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from foundry.config import load_eval_config
from foundry.evaluate.evaluate import evaluate

RESULT_FILES = ("rubric.json", "scores.json", "report.md")


def score_once(case: Path, dest: Path, cfg) -> dict:
    shutil.copytree(case, dest)
    for name in RESULT_FILES:
        (dest / name).unlink(missing_ok=True)
    scores = evaluate(dest, cfg)
    out = scores["output"]
    return {"output": out["score"], "criteria": {c["id"]: c["score"] for c in out["criteria"]},
            "cost": (scores.get("evaluation") or {}).get("cost_usd")}


def check(results: dict[str, list[dict]], expected: dict) -> list[tuple[str, bool, str]]:
    checks = []
    means = {c: statistics.mean(r["output"] for r in rs) for c, rs in results.items()}
    for case, exp in expected["cases"].items():
        if case not in results:
            checks.append((f"band {case}", False, "case not scored"))
            continue
        lo, hi = expected["bands"][exp["band"]]
        checks.append((f"band {case}", lo <= means[case] <= hi, f"mean {means[case]:.2f}, want {exp['band']} [{lo}, {hi}]"))
        for crit, cap in exp.get("criteria_max", {}).items():
            seen = [r["criteria"].get(crit) for r in results[case]]
            checks.append((f"cap {case} {crit}", all(s is not None and s <= cap for s in seen), f"scores {seen}, cap {cap}"))
    order = [c for c in expected["order"] if c in means]
    ok = all(means[a] > means[b] for a, b in zip(order, order[1:]))
    checks.append(("order " + " > ".join(order), ok, ", ".join(f"{c} {means[c]:.2f}" for c in order)))
    for case, rs in results.items():
        spread = max(r["output"] for r in rs) - min(r["output"] for r in rs)
        crit_spread = max((max(r["criteria"][k] for r in rs) - min(r["criteria"][k] for r in rs))
                          for k in rs[0]["criteria"])
        ok = spread <= expected["max_spread"]["output"] + 1e-9 and crit_spread <= expected["max_spread"]["criterion"]
        checks.append((f"consistency {case}", ok, f"output spread {spread:.2f}, max criterion spread {crit_spread}"))
    return checks


def to_markdown(cfg_path: str, judge_id: str, results: dict, checks: list, repeats: int) -> str:
    lines = [f"Judge `{judge_id}` (config `{cfg_path}`), {repeats} repeats per case.", "",
             "| Case | Output scores | Mean | Criteria (repeat 1) |", "|---|---|---|---|"]
    for case, rs in results.items():
        scores = " / ".join(f"{r['output']:.2f}" for r in rs)
        crit = ", ".join(f"{k} {v}" for k, v in rs[0]["criteria"].items())
        lines.append(f"| {case} | {scores} | {statistics.mean(r['output'] for r in rs):.2f} | {crit} |")
    lines += ["", "| Check | Result | Detail |", "|---|---|---|"]
    lines += [f"| {name} | {'pass' if ok else '**FAIL**'} | {detail} |" for name, ok, detail in checks]
    costs = [r["cost"] for rs in results.values() for r in rs if r["cost"] is not None]
    if costs:
        lines += ["", f"Evaluation cost: ${sum(costs):.2f} for {len(costs)} evaluations."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="config/bedrock.toml")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--cases", default="fixtures/judge-cases")
    ap.add_argument("--out", help="where the scored copies go (default: a new temp directory)")
    ap.add_argument("--md", help="also write the results table to this file")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args(argv)

    cfg = load_eval_config(args.config)
    cfg = cfg.model_copy(update={"storage": cfg.storage.model_copy(update={"s3_uri": ""})})  # never mirror copies
    cases_dir = Path(args.cases)
    expected = json.loads((cases_dir / "expected.json").read_text())
    out = Path(args.out or tempfile.mkdtemp(prefix="judge-sanity-"))
    jobs = [(cases_dir / case, out / f"r{i + 1}" / case) for case in expected["cases"] for i in range(args.repeats)]

    with ThreadPoolExecutor(args.concurrency) as pool:
        scored = list(pool.map(lambda job: score_once(*job, cfg), jobs))
    results: dict[str, list[dict]] = {}
    for (case, _), r in zip(jobs, scored):
        results.setdefault(case.name, []).append(r)

    checks = check(results, expected)
    md = to_markdown(args.config, cfg.judge.id, results, checks, args.repeats)
    print(md)
    print(f"Scored copies: {out}")
    if args.md:
        Path(args.md).write_text(md)
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
