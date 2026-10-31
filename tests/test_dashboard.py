"""Dashboard (increment-6 D1-D7): aggregation from run directories, the page, and untrusted inputs."""

import json
import shutil

import pytest

from conftest import FIXTURES, ROOT, classic_client
from eval_helpers import eval_config, load

from foundry.contract import Step
from foundry.evaluate.evaluate import evaluate
from foundry.ui.app import create_app
from foundry.ui.dashboard import MAX_COLOURED, build

BASE = "http://127.0.0.1:8765"


def _client(runs):
    return classic_client(create_app(runs, ROOT, port=8765), base_url=BASE, follow_redirects=False)


def _seed(runs, name, as_=None, model=None, prompt=None):
    dest = runs / (as_ or f"fixture-{name}")
    shutil.copytree(FIXTURES / name, dest)
    if model or prompt:
        rj = json.loads((dest / "run.json").read_text())
        rj["model"]["id"] = model or rj["model"]["id"]
        rj["prompt_id"] = prompt or rj["prompt_id"]
        (dest / "run.json").write_text(json.dumps(rj))
    return dest


def _entry(run_id, model, overall, cost, status="succeeded", criteria=(), missing=(), errors=()):
    steps = [Step(step=1, type="llm_call", name="model", started_at="2026-10-30T10:00:00.000Z", duration_ms=1,
                  status="ok", cost_usd=cost)]
    steps += [Step(step=i + 2, type="tool_call", name=t, server="roda", started_at="2026-10-30T10:00:01.000Z",
                   duration_ms=1, status="error", error="x") for i, t in enumerate(errors)]
    scores = None
    if overall is not None:
        scores = {"overall": overall, "evaluation": {"cost_usd": 0.01},
                  "workflow": {"score": 1.0, "sequence": {"stages": [["search"], ["details"]], "missing": list(missing)}},
                  "output": {"score": 1.0, "criteria": [{"id": k, "score": v, "max": 4} for k, v in criteria]}}
    return {"run_id": run_id, "run": {"model": {"id": model}, "prompt_id": "adb-001", "status": status},
            "scores": scores, "steps": steps}


def test_kpis_and_leaderboard_order():  # D2, D3
    d = build([_entry("a", "m-low", 0.5, 0.10), _entry("b", "m-high", 0.9, 0.20), _entry("c", "m-high", 0.7, 0.30),
               _entry("d", "m-none", None, 0.05, status="failed")])
    assert d["kpis"] == {"runs": 4, "scored": 3, "mean": pytest.approx(0.7), "spent": pytest.approx(0.68)}
    lb = d["leaderboard"]
    assert [m["model"] for m in lb] == ["m-high", "m-low", "m-none"]  # by mean; unscored last
    assert lb[0]["overall"]["min"] == 0.7 and lb[0]["overall"]["max"] == 0.9 and abs(lb[0]["cost"] - 0.25) < 1e-9
    assert (lb[2]["scored"], lb[2]["succeeded"], lb[2]["runs"]) == (0, 0, 1)


def test_scatter_colours_follow_the_model_and_fold_extra_models():  # D4
    entries = [_entry(f"{m}{i}", m, 0.8, 0.1) for m, n in (("a", 3), ("b", 2), ("c", 2), ("d", 1)) for i in range(n)]
    sc = build(entries)["scatter"]
    series = {p["model"]: (p["series"], p["shape"]) for p in sc["dots"]}
    assert series == {"a": ("s1", "circle"), "b": ("s2", "square"), "c": ("s3", "triangle"), "d": ("other", "diamond")}
    assert [item["label"] for item in sc["legend"]] == ["a", "b", "c", "Other"] and MAX_COLOURED == 3
    # Dropping a model doesn't repaint the others: slots go by name among the busiest three.
    sc2 = build([e for e in entries if e["run"]["model"]["id"] != "b"])["scatter"]
    assert {p["model"]: p["series"] for p in sc2["dots"]}["c"] == "s2"  # c is now second by name, still coloured
    assert all(sc["x0"] <= p["x"] <= sc["x1"] and sc["y0"] <= p["y"] <= sc["y1"] for p in sc["dots"])


def test_struggles_are_ranked():  # D5
    d = build([_entry("a", "m", 0.8, 0.1, criteria=[("method", 2), ("data-fit", 4)], missing=[2],
                      errors=["get_dataset_details", "get_dataset_details", "search_datasets"]),
               _entry("b", "m", 0.9, 0.1, criteria=[("method", 4), ("data-fit", 4)], missing=[2, 7])])
    assert [(t["label"], t["value"]) for t in d["tool_errors"]] == [("roda/get_dataset_details", 2),
                                                                     ("roda/search_datasets", 1)]
    assert [(m["label"], m["value"]) for m in d["missed"]] == [("adb-001 · stage 2 (details)", 2)]  # 7 is out of range
    assert [(c["label"], c["value"], c["n"]) for c in d["criteria"]] == [("method", 0.75, 2), ("data-fit", 1.0, 2)]


def test_hostile_scores_are_skipped_not_fatal():  # D7
    e = _entry("a", "m", 0.8, 0.1)
    e["scores"]["output"]["criteria"] = [{"id": "x", "score": "4", "max": 4}, {"id": ["x"], "score": 1, "max": 4},
                                         "nope", {"id": "y", "score": 1, "max": 0}]
    e["scores"]["workflow"]["sequence"] = {"stages": "nope", "missing": [1]}
    e["scores"]["evaluation"] = {"cost_usd": "lots"}
    d = build([e])
    assert d["criteria"] == [] and d["missed"] == [] and d["kpis"]["spent"] == 0.1


def test_page_from_real_runs(tmp_path):  # D1, D6
    runs = tmp_path / "runs"
    runs.mkdir()
    clean = _seed(runs, "clean")
    _seed(runs, "recovered", model="other-model")
    _seed(runs, "failed")
    evaluate(clean, load(eval_config(tmp_path / "e")))
    html = _client(runs).get("/dashboard?rubric=all").text  # all runs, incl. unscored (T3 default is active-only)
    assert 'aria-current="page">Dashboard' in html
    assert '<span class="kpi-num">3</span><span class="muted">runs' in html
    assert '<span class="kpi-num">1</span><span class="muted">scored' in html
    assert 'href="/classic/?model=scripted-a"' in html and 'href="/classic/?model=other-model"' in html
    assert 'href="/classic/runs/fixture-clean"' in html and "<summary>Table</summary>" in html
    assert "<script" not in html


def test_model_filter_on_runs_tab(tmp_path):  # D3's link target
    runs = tmp_path / "runs"
    runs.mkdir()
    _seed(runs, "clean")
    _seed(runs, "recovered", model="other-model")
    c = _client(runs)
    html = c.get("/?model=other-model").text
    assert 'href="/classic/runs/fixture-recovered"' in html and 'href="/classic/runs/fixture-clean"' not in html
    assert "Showing runs of <strong>other-model</strong>" in html
    assert 'href="/classic/runs/fixture-clean"' in c.get("/").text


def test_empty_and_hostile_runs(tmp_path):  # D7
    runs = tmp_path / "runs"
    c = _client(runs)
    assert "No runs yet" in c.get("/dashboard").text
    runs.mkdir()
    _seed(runs, "hostile", model="<img src=x onerror=alert(1)>")
    (runs / "broken").mkdir()
    (runs / "broken" / "run.json").write_text("{not json")
    r = c.get("/dashboard?rubric=all")
    assert r.status_code == 200 and "<img" not in r.text and "&lt;img src=x" in r.text
    assert 'href="/classic/?model=%3Cimg' in r.text


def test_pipeline_is_its_own_row():  # increment 7, S2
    single = _entry("a", "kimi", 0.9, 0.1)
    pipe = _entry("b", "kimi", 0.8, 0.2)
    pipe["run"]["agents"] = {"mode": "pipeline"}
    d = build([single, pipe])
    assert [m["model"] for m in d["leaderboard"]] == ["kimi", "kimi · pipeline"]
    assert {p["model"] for p in d["scatter"]["dots"]} == {"kimi", "kimi · pipeline"}


def test_filter_by_rubric(tmp_path):  # increment 8, T3
    import hashlib

    runs = tmp_path / "runs"
    runs.mkdir()
    for name in ("clean", "recovered"):
        evaluate(_seed(runs, name), load(eval_config(tmp_path / f"e-{name}")))
    # The recovered run was scored with another rubric: its own rubric.json copy and the sha in scores.json agree.
    rec = runs / "fixture-recovered"
    other_rubric = json.loads((rec / "rubric.json").read_text()) | {"rubric_id": "clay-adb", "version": "1.0"}
    (rec / "rubric.json").write_text(json.dumps(other_rubric))
    other_sha = hashlib.sha256((rec / "rubric.json").read_bytes()).hexdigest()
    sc = json.loads((rec / "scores.json").read_text())
    sc.update(rubric_sha256=other_sha, rubric_id_loaded="clay-adb", rubric_version="1.0")
    (rec / "scores.json").write_text(json.dumps(sc))
    _seed(runs, "failed")  # unscored
    c = _client(runs)
    active = hashlib.sha256((ROOT / "tasks/adb/rubric.json").read_bytes()).hexdigest()
    default = c.get("/dashboard").text
    assert '<span class="kpi-num">1</span><span class="muted">runs' in default  # only the active rubric's run
    assert 'clay-adb v1.0 (1 runs)' in default and "different rubrics" not in default
    assert '<span class="kpi-num">1</span><span class="muted">runs' in c.get(f"/dashboard?rubric={other_sha}").text
    every = c.get("/dashboard?rubric=all").text
    assert '<span class="kpi-num">3</span><span class="muted">runs' in every and "different rubrics" in every
    assert f'value="{active}"' in default
    junk = c.get("/dashboard?rubric=../../etc").text  # unknown choice falls back to the active rubric
    assert '<span class="kpi-num">1</span><span class="muted">runs' in junk


def test_empty_state_right_after_an_activation(tmp_path):  # T3: the active rubric has scored nothing yet
    runs = tmp_path / "runs"
    runs.mkdir()
    evaluate(_seed(runs, "clean"), load(eval_config(tmp_path / "e")))
    root = tmp_path / "proj"
    shutil.copytree(ROOT / "tasks", root / "tasks")
    shutil.copytree(ROOT / "config", root / "config")
    (root / "tasks/adb/rubric.json").write_text((root / "tasks/adb/rubric.json").read_text().replace("0.1", "0.2"))
    html = classic_client(create_app(runs, root, port=8765), base_url=BASE).get("/dashboard").text
    assert "No runs have been scored with this rubric yet" in html and 'href="/classic/dashboard?rubric=all"' in html
