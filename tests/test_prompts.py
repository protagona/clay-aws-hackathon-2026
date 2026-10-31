"""Increment 13: the prompts store (P3, P5, P8, P9, P11) - every test works on a copy of tasks/, never the real files."""

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from foundry.prompts.store import PromptStore, StoreError
from foundry.tasks import parse_expected, parse_prompts

ROOT = Path(__file__).resolve().parents[1]
REAL = [ROOT / "tasks/adb/prompts.jsonl", ROOT / "tasks/adb/expected.jsonl"]


@pytest.fixture(autouse=True)
def real_files_untouched():
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in REAL]
    yield
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in REAL] == before, "a test wrote the real task files"


@pytest.fixture
def store(tmp_path):
    shutil.copytree(ROOT / "tasks/adb", tmp_path / "adb", ignore=shutil.ignore_patterns("rubrics", "scripts"))
    return PromptStore(tmp_path / "adb", "adb")


def test_create_and_errors(store):  # P3
    assert store.next_id(set()) == "adb-007"
    assert store.next_id({"adb-007"}) == "adb-008"  # an id with runs is never offered
    store.create("adb-007", "  Map mangroves in Indonesia.  ", set())
    assert store.get("adb-007") == {"prompt_id": "adb-007", "prompt": "Map mangroves in Indonesia.", "archived": False, "stages": None}
    for pid, text, field, msg in [("adb-007", "x", "prompt_id", "already a prompt"), ("new", "x", "prompt_id", "reserved"),
                                  (".", "x", "prompt_id", "reserved"), ("a/b", "x", "prompt_id", "letters"),
                                  ("a..b", "x", "prompt_id", "letters"), ("x" * 65, "x", "prompt_id", "letters"),
                                  ("adb-008", "   ", "prompt", "empty"), ("adb-008", "y" * 20_001, "prompt", "limit"),
                                  ("adb-001", "x", "prompt_id", "already a prompt")]:
        with pytest.raises(StoreError, match=msg) as e:
            store.create(pid, text, set())
        assert e.value.status == 400 and e.value.field == field
    with pytest.raises(StoreError, match="already has runs"):
        store.create("adb-009", "x", {"adb-009"})


def test_store_keeps_other_lines_verbatim(store):  # P11: extra keys, CRLF and blank lines survive; only the target changes
    p = store.prompts_path
    lines = p.read_bytes().split(b"\n")
    lines[1] = lines[1].replace(b'{"prompt_id"', b'{"owner": "clay", "prompt_id"') + b"\r"
    p.write_bytes(b"\n".join(lines[:3] + [b""] + lines[3:]))
    before = p.read_bytes().split(b"\n")
    store.set_archived("adb-003", True)
    after = p.read_bytes().split(b"\n")
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(before) == len(after) and len(changed) == 1
    assert json.loads(after[changed[0]]) == {**json.loads(before[changed[0]]), "archived": True}
    assert after[1].endswith(b"\r") and b'"owner": "clay"' in after[1]
    store.set_archived("adb-003", False)
    assert p.read_bytes() == b"\n".join(before)


def test_text_with_line_separators_roundtrips(store):  # pass-3 blocker: U+2028/U+2029/U+0085 must not split a line
    text = "first second third\x85fourth"
    store.create("adb-007", text, set())
    assert store.get("adb-007")["prompt"] == text
    assert len([ln for ln in store.prompts_path.read_text(encoding="utf-8").split("\n") if ln.strip()]) == 7


def test_new_version_copies_text_and_steps(store):  # P5
    assert store.copy_version("adb-001", set()) == "adb-001-v2"
    v2 = store.get("adb-001-v2")
    assert v2["prompt"] == store.get("adb-001")["prompt"] and v2["stages"] == store.get("adb-001")["stages"]
    assert store.copy_version("adb-001-v2", set()) == "adb-001-v3"  # counts from the base id
    assert store.copy_version("adb-002", {"adb-002-v2"}) == "adb-002-v3"  # skips a -vN that has runs
    store.create("a" * 62, "x", set())
    with pytest.raises(StoreError, match="too long"):
        store.copy_version("a" * 62, set())


def test_steps_roundtrip_limits_and_removal(store):  # P8, P9
    sha = store.set_stages("adb-006", [["search_datasets", "search_datasets", "roda/list_datasets"], ["stac/search_items"]])
    exp = parse_expected(store.expected_path)["adb-006"]
    assert exp.stages == [["search_datasets", "roda/list_datasets"], ["stac/search_items"]] and exp.sha256 == sha
    for bad, msg in [([[f"t{i}"] for i in range(13)], "At most 12"), ([[f"t{i}" for i in range(13)]], "more than 12"),
                     ([["ok"], []], "Step 2 has no tools"), ([["bad tool!"]], '"bad tool!"'), ("x", "list of steps")]:
        with pytest.raises(StoreError, match=msg) as e:
            store.set_stages("adb-006", bad)
        assert e.value.status == 400 and e.value.field == "stages"
    store.set_stages("adb-006", [])
    assert "adb-006" not in parse_expected(store.expected_path)
    with pytest.raises(StoreError) as e:
        store.set_stages("nope", [["x"]])
    assert e.value.status == 404


def test_store_lock_and_atomic(store):  # P11
    (store.dir / ".prompts.lock").write_text(str(os.getpid()))
    with pytest.raises(StoreError, match="being changed") as e:
        store.create("adb-007", "x", set())
    assert e.value.status == 409
    (store.dir / ".prompts.lock").write_text("999999")  # a crashed writer's lock is cleared
    store.create("adb-007", "x", set())
    assert not (store.dir / ".prompts.lock").exists() and not list(store.dir.glob("*.tmp-*"))


def test_hand_edit_detected(store, monkeypatch):  # a hand edit between our read and our replace is refused, not lost
    import foundry.prompts.store as mod

    real = mod.parse_prompts

    def edit_meanwhile(path):
        store.prompts_path.write_bytes(store.prompts_path.read_bytes() + b'{"prompt_id": "hand", "prompt": "x"}\n')
        return real(path)

    monkeypatch.setattr(mod, "parse_prompts", edit_meanwhile)
    with pytest.raises(StoreError, match="changed on disk") as e:
        store.create("adb-007", "x", set())
    assert e.value.status == 409 and "adb-007" not in store.prompts_path.read_text()


def test_store_errors_use_relative_paths(store):  # P12: messages name tasks/adb/prompts.jsonl, never the temp file or root
    store.prompts_path.write_text(store.prompts_path.read_text() + "{not json\n")
    with pytest.raises(StoreError) as e:
        store.rows()
    assert "tasks/adb/prompts.jsonl:7" in e.value.message and str(store.dir) not in e.value.message
    dup = store.prompts_path.read_text().replace("{not json\n", '{"prompt_id": "adb-001", "prompt": "again"}\n')
    store.prompts_path.write_text(dup)
    with pytest.raises(StoreError, match="adb-001 on lines 1, 7"):
        store.set_archived("adb-002", True)


def test_archived_flag_is_exactly_true(store):
    store.prompts_path.write_text(store.prompts_path.read_text().replace('"adb-002",', '"adb-002", "archived": "false",', 1))
    assert not next(p for p in parse_prompts(store.prompts_path) if p.prompt_id == "adb-002").archived


# --- the API (P1-P4, P6, P7, P10, P12), through the real app on a copy of the project -----------------------------

from conftest import FIXTURES  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from foundry.ui.app import create_app  # noqa: E402

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def api(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    shutil.copytree(ROOT / "tasks", root / "tasks", ignore=shutil.ignore_patterns("drafts", "sources"))
    shutil.copytree(ROOT / "config", root / "config")
    monkeypatch.setenv("FOUNDRY_HOME", str(root))
    runs = tmp_path / "runs"
    runs.mkdir()
    shutil.copytree(FIXTURES / "clean", runs / "fixture-clean")  # a run of adb-001
    app = create_app(runs, root, port=8765)
    c = TestClient(app, base_url=BASE, follow_redirects=False)
    csrf = c.get("/api/session").json()["csrf"]
    return app, c, {"x-csrf-token": csrf}, root


def test_prompts_list_counts_and_create(api):  # P1, P2, P3
    _, c, h, root = api
    body = c.get("/api/prompts").json()
    rows = {p["prompt_id"]: p for p in body["prompts"]}
    assert len(rows) == 6 and rows["adb-001"]["runs"] == 1 and rows["adb-002"]["runs"] == 0
    assert rows["adb-001"]["stages"] and body["next_id"] == "adb-007" and body["root_name"] == "proj"
    r = c.post("/api/prompts", headers=h, json={"prompt_id": "adb-007", "prompt": "Map mangroves."})
    assert r.status_code == 201 and r.json() == {"prompt_id": "adb-007"}
    assert "adb-007" in c.get("/api/session").json()["prompts"]
    r = c.post("/api/prompts", headers=h, json={"prompt_id": "adb-007", "prompt": "again"})
    assert r.status_code == 400 and r.json()["field"] == "prompt_id"
    r = c.post("/api/prompts", headers=h, json={"prompt_id": "adb-008", "prompt": ""})
    assert r.status_code == 400 and r.json()["field"] == "prompt"


def test_prompt_text_locked_with_runs(api):  # P4, P6
    _, c, h, _ = api
    d = c.get("/api/prompts/adb-001").json()
    assert d["locked"] and d["runs_total"] == 1 and d["runs"][0]["run_id"] == "fixture-clean"
    assert d["next_version_id"] == "adb-001-v2"
    r = c.put("/api/prompts/adb-001", headers=h, json={"prompt": "changed"})
    assert r.status_code == 409 and r.json()["error"] == "adb-001 has 1 run, so its text can't change. Make a new version instead."
    assert c.put("/api/prompts/adb-002", headers=h, json={"prompt": "A new wording."}).status_code == 200
    assert c.get("/api/prompts/adb-002").json()["prompt"]["prompt"] == "A new wording."
    r = c.post("/api/prompts/adb-001/version", headers=h, json={})
    assert r.status_code == 201 and r.json()["prompt_id"] == "adb-001-v2"
    assert not c.get("/api/prompts/adb-001-v2").json()["locked"]


def test_archive_hides_from_session_and_refuses_runs(api):  # P7
    _, c, h, root = api
    assert c.post("/api/prompts/adb-002/archive", headers=h, json={"archived": True}).status_code == 200
    assert "adb-002" not in c.get("/api/session").json()["prompts"]
    r = c.post("/api/runs", headers=h, json={"prompt_id": "adb-002", "config": "offline"})
    assert r.status_code == 400 and "adb-002 is archived" in r.json()["error"]
    r = c.post("/api/batches", headers=h, json={"config": "offline", "prompts": ["adb-002"], "max_cost": 1})
    assert r.status_code == 400 and "archived" in r.json()["error"]
    from foundry.tasks import load_prompts
    assert any(p.prompt_id == "adb-002" for p in load_prompts("adb"))  # the CLI still resolves it (FOUNDRY_HOME copy)
    assert c.post("/api/prompts/adb-002/archive", headers=h, json={"archived": False}).status_code == 200
    assert "adb-002" in c.get("/api/session").json()["prompts"]


def test_scripted_config_without_script_refused(api):  # P10
    _, c, h, _ = api
    assert c.post("/api/prompts", headers=h, json={"prompt_id": "adb-007", "prompt": "New."}).status_code == 201
    r = c.post("/api/runs", headers=h, json={"prompt_id": "adb-007", "config": "offline"})
    assert r.status_code == 400 and r.json()["error"] == "The offline config 'offline' has no script for adb-007. Pick a Bedrock config."
    r = c.post("/api/batches", headers=h, json={"config": "offline", "prompts": ["adb-001", "adb-007"], "max_cost": 1})
    assert r.status_code == 400 and "no script for adb-007" in r.json()["error"]


def test_steps_through_api_and_tools(api):  # P8, P9
    _, c, h, _ = api
    tools = c.get("/api/prompts/tools").json()["tools"]
    assert "search_datasets" in tools and any(t.startswith("roda/") for t in tools)
    r = c.put("/api/prompts/adb-006/stages", headers=h, json={"stages": [["search_datasets"], ["roda/get_dataset_details"]]})
    assert r.status_code == 200 and len(r.json()["expected_sha256"]) == 64
    assert c.get("/api/prompts/adb-006").json()["prompt"]["stages"] == [["search_datasets"], ["roda/get_dataset_details"]]
    r = c.put("/api/prompts/adb-006/stages", headers=h, json={"stages": [[f"t{i}"] for i in range(13)]})
    assert r.status_code == 400 and r.json()["field"] == "stages"


def test_prompts_must_deny(api):  # P12
    _, c, h, root = api
    assert c.post("/api/prompts", json={"prompt_id": "x", "prompt": "y"}).status_code == 403
    assert c.post("/api/prompts", headers={**h, "content-type": "text/plain"}, content="x").status_code == 415
    for bad in ("a..b", "%2e%2e%2fconfig", "x" * 70):  # a bare ".." never reaches the server: clients normalise it
        assert c.get(f"/api/prompts/{bad}").status_code == 404
        assert c.put(f"/api/prompts/{bad}", headers=h, json={"prompt": "z"}).status_code in (404, 405)
    assert c.get("/api/prompts?task=../etc").status_code == 400
    (root / "tasks/adb/prompts.jsonl").write_text((root / "tasks/adb/prompts.jsonl").read_text() + "{broken\n")
    r = c.get("/api/prompts")
    assert r.status_code == 400 and "tasks/adb/prompts.jsonl:7" in r.json()["error"] and str(root) not in r.text
    r = c.post("/api/runs", headers=h, json={"prompt_id": "adb-001", "config": "offline"})
    assert r.status_code == 400 and str(root) not in r.text


def _run(runs: Path, name: str, prompt_id: str, started: str, rubric_sha: str | None = None) -> None:
    """A copy of the scored fixture run, re-filed under a prompt id and start time (and optionally another rubric)."""
    shutil.copytree(FIXTURES / "clean", runs / name)
    rj = json.loads((runs / name / "run.json").read_text())
    rj.update(prompt_id=prompt_id, started_at=started)
    (runs / name / "run.json").write_text(json.dumps(rj))
    if rubric_sha is not None and (runs / name / "scores.json").exists():
        sc = json.loads((runs / name / "scores.json").read_text())
        sc["rubric_sha256"] = rubric_sha
        (runs / name / "scores.json").write_text(json.dumps(sc))


def test_prompt_view_runs_latest_20(api):  # P4: newest first, at most 20, with the total
    app, c, _, _ = api
    runs = app.state.ui.runs_dir
    for i in range(22):
        _run(runs, f"r{i:02d}", "adb-003", f"2026-10-{(i % 28) + 1:02d}T10:00:00Z")
    d = c.get("/api/prompts/adb-003").json()
    assert d["runs_total"] == 22 and len(d["runs"]) == 20
    assert [r["started_at"] for r in d["runs"]] == sorted((r["started_at"] for r in d["runs"]), reverse=True)
    assert d["runs"][0]["started_at"].startswith("2026-10-22")


def test_prompts_mean_uses_active_rubric_only(api):  # P2: a run scored with another rubric counts as a run, not in the mean
    from foundry.evaluate.evaluate import evaluate

    app, c, _, _ = api
    runs = app.state.ui.runs_dir
    from eval_helpers import eval_config, load
    evaluate(runs / "fixture-clean", load(eval_config(runs.parent / "e")))
    _run(runs, "other-rubric", "adb-001", "2026-10-30T10:00:00Z", rubric_sha="0" * 64)
    row = next(p for p in c.get("/api/prompts").json()["prompts"] if p["prompt_id"] == "adb-001")
    shares = {r["run_id"]: r["kpi"]["overall"] for r in c.get("/api/runs").json()["runs"]}
    assert row["runs"] == 2 and row["scored"] == 1 and shares["other-rubric"] is None
    assert row["mean"] == pytest.approx(shares["fixture-clean"])


def test_scripted_config_with_script_allowed(api):  # P10, the positive case: the offline config has adb-001's script
    app, c, h, _ = api
    r = c.post("/api/runs", headers=h, json={"prompt_id": "adb-001", "config": "offline"})
    assert r.status_code == 201, r.text
    (job,) = app.state.ui.jobs.values()
    job.proc.kill()  # the check is what's under test, not the run itself
    job.proc.wait(10)


def test_prompts_api_lock_bytes_echo_and_origin(api):  # P11, P12 through the API
    app, c, h, root = api
    path = root / "tasks/adb/prompts.jsonl"
    (root / "tasks/adb/.prompts.lock").write_text(str(os.getpid()))
    r = c.post("/api/prompts", headers=h, json={"prompt_id": "adb-007", "prompt": "x"})
    assert r.status_code == 409 and r.json()["error"] == "Prompts are being changed; try again"
    (root / "tasks/adb/.prompts.lock").unlink()
    before = path.read_bytes()  # an invalid write (a hand-made duplicate elsewhere) is refused and changes nothing
    path.write_bytes(before + b'{"prompt_id": "adb-001", "prompt": "twin"}\n')
    broken = path.read_bytes()
    assert c.post("/api/prompts/adb-002/archive", headers=h, json={"archived": True}).status_code == 400
    assert path.read_bytes() == broken
    path.write_bytes(before)
    r = c.post("/api/prompts", headers=h, json={"prompt_id": "adb-007", "prompt": "<script>alert(1)</script>"})
    assert r.status_code == 201
    r = c.get("/api/prompts/adb-007")
    assert r.headers["content-type"] == "application/json" and r.json()["prompt"]["prompt"] == "<script>alert(1)</script>"
    assert c.post("/api/prompts", headers={**h, "origin": "http://evil.example"}, json={"prompt_id": "adb-008", "prompt": "x"}).status_code == 403
    assert c.put("/api/prompts/adb-007", headers={**h, "host": "evil.example"}, json={"prompt": "y"}).status_code == 400
    r = c.post("/api/prompts", headers={**h, "content-type": "application/json"},
               content='{"prompt_id": "adb-009", "prompt": "a\\ud800b"}')  # escaped: clients can't encode it raw
    assert r.status_code == 400 and r.json()["field"] == "prompt"  # a lone surrogate: 400, not a 500


def test_removing_steps_refuses_duplicate_lines(store):  # verify finding: an older twin line must not come back
    store.expected_path.write_text(store.expected_path.read_text() + '{"prompt_id": "adb-006", "stages": [["old_tool"]]}\n')
    with pytest.raises(StoreError, match="adb-006 on lines"):
        store.set_stages("adb-006", [])
