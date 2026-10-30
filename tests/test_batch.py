"""`foundry batch` (increment 2: B1-B7), offline against the fault MCP server and scripted models."""

import argparse
import csv
import json
import shutil

from conftest import foundry
from harness_helpers import write_config

from foundry import batch as batch_mod

ECHO = [{"tool": "echo", "input": {"text": "x"}, "usage": {"in": 1000, "out": 100}}, {"text": "done", "usage": {"in": 1000, "out": 100}}]
PRICED = '\n[prices]\n"scripted-test" = { input = 10.0, output = 100.0 }\n'  # 2 calls x $0.02 = $0.04 per run


def _cfg(tmp_path, name, turns=ECHO, **kw):
    path = write_config(tmp_path / f"src-{name}", turns, **kw)
    dest = tmp_path / f"{name}.toml"
    shutil.copy(path, dest)
    return dest


def _args(out, configs, prompts="adb-001", **kw):
    ns = dict(config=[str(c) for c in configs], models=None, prompts=prompts, repeats=1, concurrency=2,
              evaluate=False, eval_config=None, max_cost=None, out=str(out))
    ns.update(kw)
    return argparse.Namespace(**ns)


def _rows(out):
    with open(out / "summary.csv") as f:
        return list(csv.DictReader(f))


def test_variants_prompts_repeats_and_summary(tmp_path):  # B1, B5
    a, b = _cfg(tmp_path, "alpha"), _cfg(tmp_path, "beta")
    out = tmp_path / "batch"
    assert batch_mod.main(_args(out, [a, b], prompts="adb-001,adb-002", repeats=2, evaluate=True)) == 0
    rows = _rows(out)
    assert len(rows) == 8 and {r["status"] for r in rows} == {"succeeded"}
    assert sorted({r["variant"] for r in rows}) == ["alpha", "beta"]
    assert {(r["prompt"], r["repeat"]) for r in rows} == {("adb-001", "1"), ("adb-001", "2"), ("adb-002", "1"), ("adb-002", "2")}
    assert all(r["model_calls"] == "2" and r["tool_calls"] == "1" and r["overall"] for r in rows)
    assert sorted(p.name for p in (out / "runs").iterdir()) == sorted(
        f"{v}_{p}_r{k}" for v in ("alpha", "beta") for p in ("adb-001", "adb-002") for k in (1, 2))
    md = (out / "summary.md").read_text()
    assert "| alpha | scripted-test | 4/4 |" in md and "| beta | scripted-test | 4/4 |" in md
    assert "| Mean | Min | Max | Stdev |" in md  # D-7: identical scripted runs -> zero spread
    alpha = next(ln for ln in md.splitlines() if ln.startswith("| alpha |")).split(" | ")
    assert alpha[3] == alpha[4] == alpha[5] and alpha[6] == "0.00"  # mean, min, max, stdev


def test_spread():  # D-7
    assert batch_mod.spread([]) == {"mean": None, "min": None, "max": None, "stdev": None}
    assert batch_mod.spread([0.8]) == {"mean": 0.8, "min": 0.8, "max": 0.8, "stdev": None}  # stdev needs 2 runs
    s = batch_mod.spread([0.92, 0.96, 1.0])
    assert (s["min"], s["max"]) == (0.92, 1.0) and round(s["mean"], 2) == 0.96 and round(s["stdev"], 2) == 0.04


def test_models_derive_configs_differing_only_in_model_id(tmp_path):  # B1
    base = _cfg(tmp_path, "base")
    out = tmp_path / "batch"
    assert batch_mod.main(_args(out, [base], models="model-a,model.b:1")) == 0
    derived = sorted((out / "configs").glob("*.toml"))
    assert [p.name for p in derived] == ["model-a.toml", "model.b-1.toml"]
    base_lines = base.read_text().splitlines()
    for p, model in zip(derived, ("model-a", "model.b:1"), strict=True):
        lines = p.read_text().splitlines()[1:]  # first line is the provenance comment
        diff = [(x, y) for x, y in zip(base_lines, lines, strict=True) if x != y]
        assert diff == [('id = "scripted-test"', f'id = "{model}"')]
    assert {r["model"] for r in _rows(out)} == {"model-a", "model.b:1"}
    for run_json in (out / "runs").glob("*/run.json"):
        assert json.loads(run_json.read_text())["config"].endswith(".toml")


def test_concurrency_cap(tmp_path):  # B4
    slow = _cfg(tmp_path, "slow", [{"tool": "sleep", "input": {"seconds": 1}}, {"text": "done"}])
    out = tmp_path / "batch"
    jobs = batch_mod.plan([str(slow)], [], "adb-001,adb-002", 2, out)
    b = batch_mod.Batch(jobs, out, concurrency=2, evaluate=False, eval_config=str(slow), max_cost=None, log=lambda *_: None)
    assert b.run() == 0
    assert b.max_active == 2  # it did run in parallel, and never more than the cap


def test_budget_stops_launching(tmp_path):  # B3, B6
    cfg = _cfg(tmp_path, "priced", extra=PRICED)
    out = tmp_path / "batch"
    code = batch_mod.main(_args(out, [cfg], prompts="adb-001,adb-002,adb-003,adb-004", concurrency=1, max_cost=0.05))
    rows = _rows(out)
    ran = [r for r in rows if r["status"] == "succeeded"]
    assert code == 1 and len(ran) == 2  # $0.04 < $0.05 so a 2nd run launches; $0.08 >= $0.05 stops the rest
    assert [r["status"] for r in rows if r not in ran] == ["skipped (budget)"] * 2
    assert abs(sum(float(r["cost_usd"]) for r in ran) - 0.08) < 1e-9


def test_resume_skips_finished_and_retries_crashed(tmp_path):  # B2
    cfg = _cfg(tmp_path, "alpha")
    out = tmp_path / "batch"
    args = _args(out, [cfg], prompts="adb-001,adb-002")
    assert batch_mod.main(args) == 0
    first = {p.name: (p / "run.json").stat().st_mtime_ns for p in (out / "runs").iterdir()}
    assert batch_mod.main(args) == 0  # nothing to do
    assert {p.name: (p / "run.json").stat().st_mtime_ns for p in (out / "runs").iterdir()} == first
    crashed = out / "runs" / "alpha_adb-002_r1"
    rj = json.loads((crashed / "run.json").read_text())
    rj["status"] = "running"
    (crashed / "run.json").write_text(json.dumps(rj))
    assert batch_mod.main(args) == 0
    assert (out / "runs" / "alpha_adb-002_r1-retry1" / "run.json").exists() and crashed.exists()
    row = [r for r in _rows(out) if r["prompt"] == "adb-002"][0]
    assert row["status"] == "succeeded" and row["run_dir"].endswith("alpha_adb-002_r1-retry1")


def test_job_that_cannot_run_is_an_error_not_a_crash(tmp_path):  # B6
    bad = _cfg(tmp_path, "bad", [{"text": "x"}])
    bad.write_text(bad.read_text().replace(str(tmp_path / "src-bad" / "script.json"), "/nonexistent.json"))
    out = tmp_path / "batch"
    assert batch_mod.main(_args(out, [bad])) == 1
    assert _rows(out)[0]["status"] == "error"


def test_usage_errors_exit_2(tmp_path):  # B6
    a, b = _cfg(tmp_path, "alpha"), _cfg(tmp_path, "beta")
    assert batch_mod.main(_args(tmp_path / "o1", [a], prompts="adb-999")) == 2
    assert batch_mod.main(_args(tmp_path / "o2", [a, b], models="x,y")) == 2
    assert batch_mod.main(_args(tmp_path / "o3", [tmp_path / "missing.toml"])) == 2
    assert batch_mod.main(_args(tmp_path / "o4", [a], concurrency=0)) == 2


def test_cli_entry_point(tmp_path):  # B7: the real command in a fresh process
    cfg = _cfg(tmp_path, "alpha")
    out = tmp_path / "batch"
    proc = foundry("batch", "--config", str(cfg), "--prompts", "adb-001", "--out", str(out), "--evaluate")
    assert proc.returncode == 0, proc.stderr
    assert "[1/1] alpha adb-001 r1: succeeded overall" in proc.stdout and (out / "summary.md").exists()
