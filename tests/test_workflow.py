import pytest
from conftest import FIXTURES

from foundry.evaluate import workflow
from foundry.runstore import RunDir


def _score(name):
    rd = RunDir(FIXTURES / name)
    return workflow.score(rd.read_run(), rd.read_steps())


@pytest.mark.parametrize("name,expected,components", [
    ("clean", 1.0, {"completion": 1, "reliability": 1, "recovery": 1, "validity": 1, "efficiency": 1}),
    ("recovered", 0.9, {"completion": 1, "reliability": 0.5, "recovery": 1, "validity": 1, "efficiency": 1}),
    ("failed", 0.0667, {"completion": 0, "reliability": 0, "recovery": 0, "validity": 0.6667, "efficiency": 0}),
])
def test_fixture_exact_scores(name, expected, components):  # AC11
    wf = _score(name)
    assert wf["score"] == expected
    assert wf["components"] == components


def test_ordering():  # AC11
    assert _score("clean")["score"] > _score("recovered")["score"] > _score("failed")["score"]


def test_counts_recovered_fixture():
    wf = _score("recovered")
    assert (wf["tool_calls"], wf["tool_errors"], wf["retries"], wf["invalid_tool_calls"]) == (4, 2, 1, 0)
    assert wf["recovered"] is True and wf["model_calls"] == 5
    assert "reliability (2 of 4 tool calls errored)" in wf["rationale"]


def test_recovered_null_without_errors_and_default_max():
    rd = RunDir(FIXTURES / "clean")
    run = rd.read_run().model_copy(update={"limits": None})
    wf = workflow.score(run, rd.read_steps())
    assert wf["recovered"] is None and wf["max_model_calls"] == 20


def test_efficiency_curve():
    rd = RunDir(FIXTURES / "clean")
    run, steps = rd.read_run(), rd.read_steps()
    llm = steps[0]
    many = [llm.model_copy(update={"step": i}) for i in range(1, 16)]  # 15 of 20 calls
    assert workflow.score(run, many)["components"]["efficiency"] == 0.5


# --- v2 sequence component (increment-4 S1-S4) ---

from foundry.tasks import Expectation, RubricError, load_expected, load_prompts  # noqa: E402

DISCOVER = ["search_datasets", "list_datasets"]
CONFIRM = ["get_dataset_details", "preview_dataset"]


def _seq(name, stages):
    rd = RunDir(FIXTURES / name)
    return workflow.score(rd.read_run(), rd.read_steps(), Expectation(stages, "x" * 64))


def test_sequence_full_match_uses_v2_weights():
    wf = _seq("clean", [DISCOVER, CONFIRM])
    assert wf["components"]["sequence"] == 1 and wf["score"] == 1.0
    assert wf["weights"] == workflow.WEIGHTS_SEQUENCE and sum(wf["weights"].values()) == pytest.approx(1)
    assert wf["sequence"] == {"stages": [DISCOVER, CONFIRM], "matched": 2, "missing": [], "expected_sha256": "x" * 64}
    assert "called the expected tools in order" in wf["rationale"]


def test_sequence_counts_only_ok_calls_in_order():
    # recovered: search_datasets ok, preview error, preview error, preview ok
    assert _seq("recovered", [DISCOVER, CONFIRM])["sequence"]["matched"] == 2
    # order matters: confirm-before-discover is not satisfied by a later discover
    wf = _seq("clean", [CONFIRM, DISCOVER])
    assert wf["components"]["sequence"] == 0.5 and wf["sequence"]["missing"] == [2]
    assert "sequence (missed stage 2 of 2: search_datasets|list_datasets)" in wf["rationale"]
    # failed: only errored calls, so nothing matches
    assert _seq("failed", [CONFIRM])["components"]["sequence"] == 0


def test_sequence_server_qualified_names():
    assert _seq("clean", [["roda/search_datasets"]])["components"]["sequence"] == 1
    assert _seq("clean", [["stac/search_datasets"]])["components"]["sequence"] == 0


def test_v2_score_value():  # recovered: .35 + .15*.5 + .15 + .10 + .10 + .15*1
    assert _seq("recovered", [DISCOVER, CONFIRM])["score"] == 0.925


def test_adb_expectations_cover_every_prompt():
    for p in load_prompts("adb"):
        exp = load_expected("adb", p.prompt_id)
        assert exp is not None and exp.stages and len(exp.sha256) == 64
        assert not any("/" in tool for stage in exp.stages for tool in stage)  # imported runs have no server
    assert load_expected("adb", "no-such-prompt") is None


def test_golden_runs_confirm_datasets():  # S4: every golden run discovered, then confirmed, a dataset
    golden = FIXTURES / "golden"
    for d in sorted(golden.iterdir()):
        rd = RunDir(d)
        run = rd.read_run()
        wf = workflow.score(run, rd.read_steps(), load_expected(run.task_id, run.prompt_id))
        assert wf["components"]["sequence"] == 1, d.name


def test_discovery_only_trace_loses_sequence():  # S4
    rd = RunDir(FIXTURES / "clean")
    steps = [s for s in rd.read_steps() if s.name != "get_dataset_details"]
    wf = workflow.score(rd.read_run(), steps, load_expected("adb", "adb-001"))
    assert wf["components"]["sequence"] == 0.5 and wf["score"] < 1


@pytest.mark.parametrize("line,msg", [
    ("not json", "each line must be"),
    ('{"prompt_id": "../x", "stages": [["a"]]}', "prompt_id"),
    ('{"prompt_id": "p1", "stages": []}', "stages must be"),
    ('{"prompt_id": "p1", "stages": [["bad tool name"]]}', "stages must be"),
    ('{"prompt_id": "p1", "stages": [[]]}', "stages must be"),
])
def test_expected_file_errors_name_file_and_line(tmp_path, monkeypatch, line, msg):
    (tmp_path / "tasks" / "t").mkdir(parents=True)
    (tmp_path / "tasks" / "t" / "expected.jsonl").write_text('{"prompt_id": "ok", "stages": [["a"]]}\n' + line + "\n")
    monkeypatch.setenv("FOUNDRY_HOME", str(tmp_path))
    with pytest.raises(RubricError, match=r"expected\.jsonl:2: .*" + msg):
        load_expected("t", "p1")
