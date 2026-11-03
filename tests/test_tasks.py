"""Task files line up, and every ADB prompt runs offline end to end (clay-embeddings AC17, AC19)."""

import json

import pytest
from conftest import ROOT, foundry

from foundry.contract import validate_run_dir

TASK = ROOT / "tasks" / "adb"
PROMPT_IDS = [json.loads(ln)["prompt_id"] for ln in (TASK / "prompts.jsonl").read_text().splitlines() if ln.strip()]


def test_expected_and_scripts_cover_every_prompt():  # AC17
    expected = {json.loads(ln)["prompt_id"] for ln in (TASK / "expected.jsonl").read_text().splitlines() if ln.strip()}
    assert set(PROMPT_IDS) == expected
    for variant in ("scripted-a", "scripted-b", "scripted-pipeline"):
        assert {p.stem for p in (TASK / "scripts" / variant).glob("*.json")} == set(PROMPT_IDS), variant


def test_embeddings_prompts_expect_embeddings_tools():  # AC17, AC18
    stages = {json.loads(ln)["prompt_id"]: json.loads(ln)["stages"]
              for ln in (TASK / "expected.jsonl").read_text().splitlines() if ln.strip()}
    assert stages["adb-007"] == [["embedding_change"], ["search_items", "get_item"]]
    assert stages["adb-008"] == [["similar_places"], ["search_items", "get_item"]]
    system = (TASK / "system.txt").read_text()
    assert all(t in system for t in ("embedding_coverage", "similar_places", "embedding_change"))
    assert "Do not invent dataset names, buckets or licenses" in system


@pytest.mark.network
@pytest.mark.parametrize("prompt_id", PROMPT_IDS)
def test_offline_run_every_prompt(tmp_path, prompt_id):  # AC19: real RODA, STAC and S3 listings; scripted model
    runs = tmp_path / "runs"
    proc = foundry("run", "--config", "config/offline.toml", "--prompt", prompt_id, "--runs-dir", str(runs))
    assert proc.returncode == 0, proc.stderr[-2000:]
    (run_dir,) = [p for p in runs.iterdir() if p.is_dir()]
    assert not [v for v in validate_run_dir(run_dir) if v.severity == "error"]
    assert json.loads((run_dir / "run.json").read_text())["status"] == "succeeded"
