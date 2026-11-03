import importlib.util
import json

from conftest import ROOT

spec = importlib.util.spec_from_file_location("judge_sanity", ROOT / "scripts" / "judge_sanity.py")
js = importlib.util.module_from_spec(spec)
spec.loader.exec_module(js)

EXPECTED = json.loads((ROOT / "fixtures" / "judge-cases" / "expected.json").read_text())


def _r(output, paths=None, **crit):
    return {"output": output, "criteria": crit or {"data-fit": 4}, "cost": None, "paths": paths}


NF = ["not_found"] * 4


def _good():
    return {"A-strong": [_r(1.0), _r(0.92)], "B-short": [_r(0.5), _r(0.42)], "C-vague": [_r(0.0), _r(0.08)],
            "D-hallucinated": [_r(0.0, **{"data-fit": 1, "feasibility": 1})] * 2, "E-injection": [_r(0.17)] * 2,
            "F-missing-paths": [_r(0.5, NF, **{"data-fit": 1}), _r(0.42, NF, **{"data-fit": 2})],
            "F-missing-paths@nocheck": [_r(0.92, **{"data-fit": 4}), _r(1.0, **{"data-fit": 4})]}


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


def test_case_f_checks():  # requirement-gaps AC7: below A and below its own no-path-check control, paths not_found
    names = {name for name, ok, _ in js.check(_good(), EXPECTED)}
    assert {"below F-missing-paths < A-strong data-fit", "below F-missing-paths < A-strong output",
            "below F-missing-paths < F-missing-paths@nocheck data-fit",
            "below F-missing-paths < F-missing-paths@nocheck output", "paths F-missing-paths all not_found"} <= names
    assert not any(n.startswith("band F-missing-paths") for n in names)  # F has no band

    res = _good()
    res["F-missing-paths@nocheck"] = [_r(0.5, **{"data-fit": 1})] * 2  # the judge caught F without the evidence
    res["F-missing-paths"][0]["paths"] = ["not_found", "unverified", "not_found", "not_found"]  # a timeout
    failed = {name for name, ok, _ in js.check(res, EXPECTED) if not ok}
    assert failed == {"below F-missing-paths < F-missing-paths@nocheck data-fit",
                      "below F-missing-paths < F-missing-paths@nocheck output", "paths F-missing-paths all not_found"}


def test_below_is_strict_across_repeats():  # max(case) < min(other): one overlapping repeat fails it
    res = _good()
    res["F-missing-paths"] = [_r(0.5, NF, **{"data-fit": 1}), _r(0.95, NF, **{"data-fit": 1})]
    failed = {name for name, ok, _ in js.check(res, EXPECTED) if not ok}
    assert "below F-missing-paths < A-strong output" in failed


def test_case_f_differs_from_a_only_in_four_arns():
    a = json.loads((ROOT / "fixtures/judge-cases/A-strong/output.json").read_text())["text"]
    f = json.loads((ROOT / "fixtures/judge-cases/F-missing-paths/output.json").read_text())["text"]
    swaps = {"arn:aws:s3:::lp-prod-protected/HLSS30.020": "arn:aws:s3:::nasa-hls-cogs/HLSS30.020",
             "arn:aws:s3:::lp-prod-protected/HLSL30.020": "arn:aws:s3:::nasa-hls-cogs/HLSL30.020",
             "arn:aws:s3:::sentinel-cogs": "arn:aws:s3:::sentinel-s2-l2a-cogs-us-west-2",
             "arn:aws:s3:::io-10m-annual-lulc": "arn:aws:s3:::io-lulc-annual-10m"}
    for old, new in swaps.items():
        a = a.replace(old, new)
    assert a == f
    for name in ("trace.jsonl", "run.json"):
        assert (ROOT / "fixtures/judge-cases/F-missing-paths" / name).read_bytes() == \
            (ROOT / "fixtures/judge-cases/A-strong" / name).read_bytes()
