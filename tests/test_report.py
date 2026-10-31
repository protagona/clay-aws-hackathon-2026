import json
import re

from eval_helpers import eval_config, load

from foundry.engine.evaluate.evaluate import evaluate
from foundry.engine.evaluate.report import cell, esc


def test_esc_neutralizes_markdown_and_html():
    s = esc("# head <script>alert(1)</script> [x](javascript:y) ![i](https://e/x.png) *b* _i_ `c`\nnext")
    assert "<" not in s and ">" not in s
    assert "\\[x\\]" in s and "\\!\\[i\\]" in s and s.startswith("\\# head")
    assert "\n" not in s
    assert cell("a | b") == "a \\| b"


def test_hostile_fixture_escaped(tmp_path, fixture_run):  # AC10c
    run = fixture_run("hostile")
    evaluate(run, load(eval_config(tmp_path / "cfg")))
    rep = (run / "report.md").read_text()
    assert "<script>" not in rep and "&lt;script&gt;" in rep
    assert not re.search(r"(?<!\\)\]\(javascript:", rep)  # only escaped (inert) brackets precede it
    assert not re.search(r"(?<!\\)!(?<!\\)\[", rep)
    assert not re.search(r"^# Fake heading", rep, re.MULTILINE)
    timeline = rep.split("## Timeline")[1].split("## Agent output")[0]
    rows = [ln for ln in timeline.splitlines() if re.match(r"^\| \d+ \|", ln)]
    n = len((run / "trace.jsonl").read_text().splitlines())
    assert len(rows) == n
    for row in rows:  # every row keeps exactly 6 cells despite '|' in the content
        assert len(re.split(r"(?<!\\)\|", row)) == 8


def test_provenance(tmp_path, fixture_run):  # AC14
    run = fixture_run("recovered")
    s = evaluate(run, load(eval_config(tmp_path / "cfg")))
    rep = (run / "report.md").read_text()
    assert f"Evaluation id: {s['evaluation_id']}" in rep
    assert "## Workflow score: 0.93" in rep and "## Output score: 0.67" in rep  # v2: 0.925, sequence 2/2
    assert "**Overall:** 0.80" in rep
    assert json.loads((run / "scores.json").read_text())["templates"] == {"judge": "judge-v1", "summarizer": "summarizer-v1"}
