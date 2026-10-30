import importlib.util
import json

from conftest import ROOT

spec = importlib.util.spec_from_file_location("judge_sanity", ROOT / "scripts" / "judge_sanity.py")
js = importlib.util.module_from_spec(spec)
spec.loader.exec_module(js)

EXPECTED = json.loads((ROOT / "fixtures" / "judge-cases" / "expected.json").read_text())


def _r(output, **crit):
    return {"output": output, "criteria": crit or {"data-fit": 4}, "cost": None}


def _good():
    return {"A-strong": [_r(1.0), _r(0.92)], "B-short": [_r(0.5), _r(0.42)], "C-vague": [_r(0.0), _r(0.08)],
            "D-hallucinated": [_r(0.0, **{"data-fit": 1, "feasibility": 1})] * 2, "E-injection": [_r(0.17)] * 2}


def test_judge_cases_validate():  # D-6 cases are valid runs, so `evaluate` accepts them
    from foundry.contract import validate_run_dir
    for case in EXPECTED["cases"]:
        assert not [v for v in validate_run_dir(ROOT / "fixtures" / "judge-cases" / case) if v.severity == "error"]


def test_checks_pass_for_a_sane_judge():
    assert all(ok for _, ok, _ in js.check(_good(), EXPECTED))


def test_checks_catch_each_failure():
    res = _good()
    res["C-vague"] = [_r(0.75), _r(0.75)]  # the vague plan rated like a good one (what Nova 2 Lite did)
    res["D-hallucinated"] = [_r(0.0, **{"data-fit": 4, "feasibility": 1})] * 2  # fooled by the invented dataset
    res["E-injection"] = [_r(0.0), _r(1.0)]  # inconsistent
    failed = {name for name, ok, _ in js.check(res, EXPECTED) if not ok}
    assert failed == {"band C-vague", "order A-strong > B-short > C-vague", "cap D-hallucinated data-fit",
                      "band E-injection", "consistency E-injection"}


def test_offline_end_to_end(tmp_path):  # the offline judge doesn't rank, so the check must fail (exit 1)
    md = tmp_path / "out.md"
    rc = js.main(["--config", str(ROOT / "config" / "offline.toml"), "--repeats", "1",
                  "--cases", str(ROOT / "fixtures" / "judge-cases"), "--out", str(tmp_path / "out"), "--md", str(md)])
    assert rc == 1
    assert "| A-strong |" in md.read_text() and (tmp_path / "out" / "r1" / "E-injection" / "scores.json").exists()
