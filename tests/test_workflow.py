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
