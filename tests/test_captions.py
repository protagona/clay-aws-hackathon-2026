"""Caption task (requirement-gaps slice C): sample cache, naip MCP server, image trace field, embedders, retrieval
scorer, evaluate wiring and report, task pack. Uses a synthetic sample (tests/naip_fixture.py); no AWS calls."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from naip_fixture import ROOT, make_sample, scorer_sample_id

from foundry.config import EmbedderCfg, load_config
from foundry.contract import Output
from foundry.data import samples
from foundry.data.tasks import Prompt, load_prompts, parse_expected
from foundry.engine.evaluate import retrieval
from foundry.engine.evaluate.evaluate import evaluate
from foundry.engine.harness.mcp import server_env
from foundry.engine.harness.runner import run
from foundry.engine.models.embedder import EmbedderError, HashEmbedder, build_embedder


@pytest.fixture(scope="module")
def sample_dir(tmp_path_factory):
    return make_sample(tmp_path_factory.mktemp("samples") / "naip-captions")


@pytest.fixture
def samples_env(sample_dir, monkeypatch):
    monkeypatch.setenv("FOUNDRY_SAMPLES_DIR", str(sample_dir.parent))
    return sample_dir


# --- sample cache ------------------------------------------------------------------------------------------------

class _S3:
    def __init__(self, files: dict[str, bytes], etags: dict[str, str]):
        self.files, self.etags, self.gets = files, etags, []

    def head_object(self, Bucket, Key, **kw):
        if Key not in self.files:
            raise RuntimeError("404")
        return {"ETag": self.etags[Key]}

    def get_object(self, Bucket, Key, **kw):
        self.gets.append(Key)
        return {"Body": io.BytesIO(self.files[Key])}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """No local samples, a private cache dir, no state bucket."""
    monkeypatch.delenv("FOUNDRY_SAMPLES_DIR", raising=False)
    monkeypatch.delenv("STATE_BUCKET", raising=False)
    monkeypatch.setattr(samples, "_local_bases",  # only an explicitly set FOUNDRY_SAMPLES_DIR, never the repo's samples/
                        lambda: [Path(os.environ["FOUNDRY_SAMPLES_DIR"])] if os.environ.get("FOUNDRY_SAMPLES_DIR") else [])
    monkeypatch.setattr(samples.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    return tmp_path


def test_local_dirs_win(isolated, sample_dir, monkeypatch):
    monkeypatch.setenv("FOUNDRY_SAMPLES_DIR", str(sample_dir.parent))
    assert samples.ensure("naip-captions") == sample_dir


def test_download_then_refresh_on_etag_change(isolated, monkeypatch):
    monkeypatch.setenv("STATE_BUCKET", "state-bucket")
    s3 = _S3({"samples/naip-captions/sample.tif": b"tif1", "samples/naip-captions/tiles.json": b"{}"},
             {"samples/naip-captions/sample.tif": "a", "samples/naip-captions/tiles.json": "b"})
    d = samples.ensure("naip-captions", client=s3)
    assert (d / "sample.tif").read_bytes() == b"tif1" and len(s3.gets) == 2
    assert samples.ensure("naip-captions", client=s3) == d and len(s3.gets) == 2  # cached: HEADs only
    s3.files["samples/naip-captions/sample.tif"], s3.etags["samples/naip-captions/sample.tif"] = b"tif2", "c"
    samples.ensure("naip-captions", client=s3)  # re-staged in the bucket: picked up
    assert (d / "sample.tif").read_bytes() == b"tif2" and len(s3.gets) == 4
    assert not [p for p in d.iterdir() if p.name.startswith(".sample") or p.name.startswith(".tiles")]  # no temps


def test_not_staged_is_none_and_unreachable_falls_back(isolated, monkeypatch):
    monkeypatch.setenv("STATE_BUCKET", "state-bucket")
    assert samples.ensure("naip-captions", client=_S3({}, {})) is None
    good = _S3({"samples/naip-captions/sample.tif": b"t", "samples/naip-captions/tiles.json": b"{}"},
               {"samples/naip-captions/sample.tif": "a", "samples/naip-captions/tiles.json": "b"})
    d = samples.ensure("naip-captions", client=good)
    assert samples.ensure("naip-captions", client=_S3({}, {})) == d  # bucket gone: the cached copy, with a note


def test_no_bucket_no_sample(isolated):
    assert samples.ensure("naip-captions", SimpleNamespace(s3_uri="")) is None
    with pytest.raises(ValueError):
        samples.ensure("../etc")
    assert "stage_naip_tiles.py --upload" in samples.staging_hint("naip-captions")


def test_server_env_placeholder():
    cfg = SimpleNamespace(env={"NAIP_SAMPLE_DIR": "{sample:naip-captions}", "OTHER": "x"})
    assert server_env(cfg)["NAIP_SAMPLE_DIR"] == ""  # tool listing: no resolver, no download
    assert server_env(cfg, lambda n: Path(f"/cache/{n}"))["NAIP_SAMPLE_DIR"] == "/cache/naip-captions"
    assert server_env(cfg, lambda n: None)["NAIP_SAMPLE_DIR"] == ""
    assert server_env(cfg)["OTHER"] == "x"
    assert not any(k.startswith("AWS_") for k in server_env(cfg, lambda n: Path("/x")))


# --- naip server --------------------------------------------------------------------------------------------------

@pytest.fixture
def naip(sample_dir, monkeypatch):
    from foundry.engine.mcp_servers import naip as mod

    monkeypatch.setenv("NAIP_SAMPLE_DIR", str(sample_dir))
    mod._sample.cache_clear()
    yield mod
    mod._sample.cache_clear()


def test_naip_tile_png_and_metadata(naip, sample_dir):
    import time

    from rasterio.io import MemoryFile

    t0 = time.monotonic()
    img, text = naip.naip_tile("t-r03-c05")
    assert time.monotonic() - t0 < 2.0  # design: one window read plus PNG encode
    assert img.data[:8] == b"\x89PNG\r\n\x1a\n"
    with MemoryFile(img.data) as mem, mem.open() as png:
        assert (png.width, png.height, png.count, png.dtypes[0]) == (256, 256, 3, "uint8")
    meta = json.loads(text)
    tile = next(t for t in json.loads((sample_dir / "tiles.json").read_text())["tiles"] if t["tile_id"] == "t-r03-c05")
    assert meta["bbox"] == tile["bbox"] and meta["acquired"] == "2020-05-24" and meta["size_px"] == 256


@pytest.mark.parametrize("bad", ["t-r08-c00", "t-r03-c5", "../sample.tif", "", "t-r03-c05; rm"])
def test_naip_tile_rejects_bad_ids(naip, bad):
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="Tile ids look like t-r03-c05"):
        naip.naip_tile(bad)


def test_naip_missing_and_inconsistent_sample(naip, tmp_path, monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError

    monkeypatch.setenv("NAIP_SAMPLE_DIR", "")
    naip._sample.cache_clear()
    with pytest.raises(ToolError, match="NAIP sample not available"):
        naip.naip_tile("t-r00-c00")
    other = make_sample(tmp_path / "other", seed=1)
    meta = json.loads((other / "tiles.json").read_text())
    for t in meta["tiles"]:
        t["bbox"] = [v + 0.01 for v in t["bbox"]]  # footprints of a different staging
    (other / "tiles.json").write_text(json.dumps(meta))
    monkeypatch.setenv("NAIP_SAMPLE_DIR", str(other))
    naip._sample.cache_clear()
    with pytest.raises(ToolError, match="inconsistent"):
        naip.naip_tile("t-r00-c00")


# --- through the real harness: config, placeholder, server, image trace field, evaluate --------------------------

def test_offline_caption_run_end_to_end(tmp_path, samples_env):  # AC10, AC11 (trace), AC12, AC15, AC16
    cfg = load_config(ROOT / "config" / "offline-captions.toml")
    prompt = next(p for p in load_prompts("captions") if p.prompt_id == "cap-05")
    run_dir, status = run(cfg, prompt, tmp_path / "runs")
    assert status == "succeeded"
    steps = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()]
    tool = next(s for s in steps if s["type"] == "tool_call")
    assert tool["name"] == "naip_tile" and tool["status"] == "ok" and tool["images"] == 1
    assert json.loads((run_dir / "run.json").read_text())["reference_ids"] == ["captions-stand-in-v0"]
    scores = evaluate(run_dir, cfg)
    out = scores["output"]
    assert out["scorer"] == "retrieval-v1" and out["judge"] is None
    assert scores["templates"]["judge"] is None and scores["evaluation"]["judge"] is None
    assert scores["evaluation"]["embedder"] == {"provider": "hash", "id": "hash-bow-512"}
    assert out["criteria"][0]["score"] / out["criteria"][0]["max"] == pytest.approx(out["score"], abs=0.01)
    assert scores["workflow"]["sequence"]["matched"] == 1
    rec = json.loads((run_dir / "retrieval.json").read_text())
    assert rec["target"] == "t-r03-c03" and len(rec["candidates"]) == 8
    assert scores["retrieval_sha256"]
    report = (run_dir / "report.md").read_text()
    assert "## Retrieval" in report and "Judge: not used (retrieval scorer)" in report


def test_caption_run_without_sample_notes_how_to_stage(tmp_path, isolated):
    cfg = load_config(ROOT / "config" / "offline-captions.toml")
    run_dir, _ = run(cfg, Prompt("cap-05", "Write a retrieval caption for NAIP tile t-r03-c03."), tmp_path / "runs")
    steps = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()]
    note = next(s for s in steps if s["type"] == "note" and s["name"] == "sample")
    assert "stage_naip_tiles.py --upload" in note["text"]
    tool = next(s for s in steps if s["type"] == "tool_call")
    assert tool["status"] == "error" and "NAIP sample not available" in tool["error"]


def test_cap_rebuild_keeps_images():
    from foundry.engine.harness.hooks import Limits, TraceHooks

    class _Tracers:
        steps: list = []

        def record(self, **kw):
            return SimpleNamespace(step=1, **kw)

    hooks = TraceHooks(tracers=_Tracers(), limits=Limits(10, 10_000, 20), model_id="m", prices={},
                       tool_server={"naip_tile": "naip"}, tool_schema={}, error_detection={})
    image = {"image": {"format": "png", "source": {"bytes": b"x"}}}
    event = SimpleNamespace(tool_use={"toolUseId": "1", "name": "naip_tile", "input": {}}, cancel_message=None,
                            exception=None, duration=0.1,
                            result={"toolUseId": "1", "status": "success", "content": [image, {"text": "y" * 100}]})
    hooks.after_tool(event)
    assert image in event.result["content"] and "[truncated" in event.result["content"][0]["text"]


# --- embedders ----------------------------------------------------------------------------------------------------

def test_hash_embedder_deterministic_unit():
    e = HashEmbedder(EmbedderCfg(provider="hash", id="h", dimensions=64))
    a, b = e.embed(["Irrigated fields beside a canal", "irrigated field beside a canals"])
    assert a == HashEmbedder(EmbedderCfg(provider="hash", id="h", dimensions=64)).embed(["Irrigated fields beside a canal"])[0]
    assert sum(x * x for x in a) == pytest.approx(1.0) and sum(x * y for x, y in zip(a, b)) == pytest.approx(1.0)


def test_embedder_config_validation():
    with pytest.raises(ValueError):
        EmbedderCfg(provider="bedrock", id="amazon.titan-embed-text-v2:0", dimensions=384)
    assert EmbedderCfg(provider="bedrock", id="x", dimensions=512).dimensions == 512


def test_bedrock_embedder_request_and_budget(monkeypatch):
    import boto3

    bodies = []

    class _Client:
        def invoke_model(self, modelId, body):
            bodies.append((modelId, json.loads(body)))
            return {"body": io.BytesIO(json.dumps({"embedding": [1.0] + [0.0] * 255, "inputTextTokenCount": 7}).encode())}

    monkeypatch.setattr(boto3, "client", lambda *a, **k: _Client())
    e = build_embedder(EmbedderCfg(provider="bedrock", id="amazon.titan-embed-text-v2:0", dimensions=256))
    assert e.embed(["a", "b"])[0][0] == 1.0 and e.calls == [7, 7]
    assert bodies[0] == ("amazon.titan-embed-text-v2:0", {"inputText": "a", "dimensions": 256, "normalize": True})
    slow = build_embedder(EmbedderCfg(provider="bedrock", id="x", dimensions=256, budget_s=0.001))
    monkeypatch.setattr("foundry.engine.models.embedder.time.monotonic", iter([0.0, 0.0, 5.0]).__next__)
    with pytest.raises(EmbedderError, match="budget"):
        slow.embed(["a", "b"])


def test_every_config_has_an_embedder():  # AC14
    for f in sorted((ROOT / "config").glob("*.toml")):
        emb = load_config(f).embedder
        assert emb is not None, f.name
        assert emb.provider == ("bedrock" if f.name.startswith("bedrock") else "hash"), f.name


# --- retrieval scorer ---------------------------------------------------------------------------------------------

def test_candidates_and_rank():
    tiles = {"a": [1, 0], "b": [0.9, 0.1], "c": [0, 1], "d": [0.95, 0.05]}
    assert [t for t, _ in retrieval.candidates(tiles, "a", 2)] == ["a", "d", "b"]
    assert retrieval.rank_of_target([0.5, 0.4, 0.3]) == 1
    assert retrieval.rank_of_target([0.5, 0.5, 0.6]) == 3  # ties count against the caption


def _score(spec, prompt_id="cap-05", text="Meandering river", kind="text", embedder="hash", prompt=None):
    emb = HashEmbedder(EmbedderCfg(provider="hash", id="h", dimensions=512)) if embedder == "hash" else embedder
    run_ = SimpleNamespace(prompt_id=prompt_id)
    return retrieval.score(spec, run_, Output(kind=kind, text=text), emb, None, prompt, "0.1")


def test_score_unscored_cases(samples_env):
    spec = retrieval.load("captions")
    assert _score(spec, prompt_id="nope")[0]["error"].startswith("no retrieval target")
    assert _score(spec, kind="none", text="")[0]["error"] == "no caption"
    assert "no [embedder]" in _score(spec, embedder=None)[0]["error"]
    assert "names a different tile" in _score(spec, prompt="Write a retrieval caption for NAIP tile t-r00-c00.")[0]["error"]

    class _Broken:
        cfg = SimpleNamespace(id="amazon.titan-embed-text-v2:0")

        def embed(self, texts):
            raise RuntimeError("throttled")

    section, record = _score(spec, embedder=_Broken())
    assert section["score"] is None and "throttled" in section["error"] and record is None


def test_score_refuses_other_staging(tmp_path, monkeypatch):
    make_sample(tmp_path / "naip-captions", sample_id="another-staging")
    monkeypatch.setenv("FOUNDRY_SAMPLES_DIR", str(tmp_path))
    assert "re-stage the sample" in _score(retrieval.load("captions"))[0]["error"]


def test_own_reference_ranks_first_for_every_target(samples_env):  # AC13, offline (hash embedder)
    spec = retrieval.load("captions")
    refs = {json.loads(line)["tile_id"]: json.loads(line)["caption"]
            for line in (ROOT / "tasks/captions/references.jsonl").read_text().splitlines()}
    for pid, target in spec.targets.items():
        section, record = _score(spec, prompt_id=pid, text=refs[target])
        assert section["score"] == 1.0, (pid, section)
        assert json.loads(record)["target"] == target


def test_malformed_scorer_stops_evaluation(tmp_path, monkeypatch):
    task = tmp_path / "tasks" / "broken"
    task.mkdir(parents=True)
    (task / "scorer.json").write_text('{"kind": "retrieval"}')
    monkeypatch.setattr("foundry.data.tasks.project_root", lambda: tmp_path)
    with pytest.raises(retrieval.ScorerError, match="missing or bad field"):
        retrieval.load("broken")
    (task / "scorer.json").write_text('{"kind": "telepathy"}')
    with pytest.raises(retrieval.ScorerError, match="not a known scorer"):
        retrieval.load("broken")


# --- task pack ----------------------------------------------------------------------------------------------------

def test_caption_task_pack():  # AC9, AC15
    from foundry.data.project import task_ids

    assert "captions" in task_ids(ROOT)
    prompts = load_prompts("captions")
    spec = json.loads((ROOT / "tasks/captions/scorer.json").read_text())
    assert [p.prompt_id for p in prompts] == [f"cap-{i:02d}" for i in range(1, 13)]
    for p in prompts:
        assert spec["targets"][p.prompt_id] in p.prompt  # each prompt names its target tile
    expected = parse_expected(ROOT / "tasks/captions/expected.jsonl")
    assert all(expected[p.prompt_id].stages == [["naip_tile"]] for p in prompts)
    refs = [json.loads(line) for line in (ROOT / "tasks/captions/references.jsonl").read_text().splitlines()]
    assert len(refs) == 64 and len({r["tile_id"] for r in refs}) == 64
    assert len({r["caption"] for r in refs}) == 64  # distinct: ties count against the caption
    assert all(r["source"].startswith("stand-in") for r in refs)
    assert spec["sample"]["sample_id"] == scorer_sample_id()
    rubric = json.loads((ROOT / "tasks/captions/rubric.json").read_text())
    assert [c["id"] for c in rubric["criteria"]] == ["retrieval"]


@pytest.mark.parametrize("prompt_id", [f"cap-{i:02d}" for i in range(1, 13)])
def test_offline_caption_every_prompt(tmp_path, samples_env, prompt_id):  # AC9, AC15: each prompt runs and scores
    cfg = load_config(ROOT / "config" / "offline-captions.toml")
    prompt = next(p for p in load_prompts("captions") if p.prompt_id == prompt_id)
    run_dir, status = run(cfg, prompt, tmp_path / "runs")
    assert status == "succeeded"
    scores = evaluate(run_dir, cfg)
    assert scores["workflow"]["sequence"]["matched"] == 1 and scores["output"]["score"] is not None


def test_malformed_sample_is_unscored_not_a_crash(tmp_path, monkeypatch):
    d = tmp_path / "naip-captions"
    make_sample(d)
    (d / "tiles.json").write_text('{"sample_id": "x"}')  # no "tiles"
    monkeypatch.setenv("FOUNDRY_SAMPLES_DIR", str(tmp_path))
    assert "tiles.json is malformed" in _score(retrieval.load("captions"))[0]["error"]
    (d / "tiles.json").write_text("not json")
    assert "tiles.json is malformed" in _score(retrieval.load("captions"))[0]["error"]


def test_bad_sample_name_in_scorer_stops_evaluation(tmp_path, monkeypatch):
    task = tmp_path / "tasks" / "broken"
    task.mkdir(parents=True)
    (task / "scorer.json").write_text(json.dumps({"kind": "retrieval", "references": "r.jsonl",
                                                  "sample": {"name": "../etc"}, "targets": {}}))
    monkeypatch.setattr("foundry.data.tasks.project_root", lambda: tmp_path)
    with pytest.raises(retrieval.ScorerError, match="sample.name"):
        retrieval.load("broken")
