"""The harness itself (backend-parity ADR-6): a run and its scoring through the API on both setups, with jobs run
by each setup's own machinery; and the rule that single-setup tests stay few (spec AC1)."""

import ast

import pytest

from conftest import ROOT
from harness_helpers import write_config

CONVERTED = ["test_api.py", "test_api_contract.py", "test_api_ports.py", "test_backends.py", "test_prompts.py",
             "test_dashboard.py", "test_tools.py"]
MAX_SINGLE_SETUP = 10


def add_config(h, name: str, turns: list[dict], **kw) -> None:
    """A scripted config on the fault server (no network), saved as config/<name>.toml of the project."""
    src = write_config(h.project.parent / f"cfg-{name}", turns, **kw)
    (h.project / "config" / f"{name}.toml").write_text(src.read_text())


def test_a_run_and_its_scoring_on_both_setups(backend):
    h = backend
    add_config(h, "ok", [{"tool": "echo", "input": {"text": "x"}}, {"text": "done"}])
    r = h.client.post("/api/runs", headers=h.headers(), json={"prompt_id": "adb-001", "config": "ok"})
    assert r.status_code == 201, r.text
    run_id = r.json()["run_id"]
    h.drain()
    d = h.client.get(f"/api/runs/{run_id}")
    assert d.status_code == 200 and d.json()["run"]["status"] == "succeeded", d.text
    assert h.client.post(f"/api/runs/{run_id}/evaluate", headers=h.headers()).status_code == 202
    h.drain()
    (card,) = [c for c in h.client.get("/api/runs").json()["runs"] if c["run_id"] == run_id]
    assert card["column"] == "scored" and card["overall"] is not None
    assert (h.pull(run_id) / "scores.json").is_file()


def test_single_setup_tests_are_few():
    marked = []
    for name in CONVERTED:
        tree = ast.parse((ROOT / "tests" / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                for dec in node.decorator_list:
                    src = ast.unparse(dec)
                    if "laptop_only" in src or "cloud_only" in src:
                        marked.append(f"{name}::{node.name}")
    assert len(marked) <= MAX_SINGLE_SETUP, marked


# --- one board (backend-parity ADR-2, spec AC4) -------------------------------------------------------------------


def _board(h) -> dict:
    """GET /api/runs without what depends on the clock (the card's `when`, the KPI `today`)."""
    body = h.client.get("/api/runs").json()
    for c in body["runs"]:
        c.pop("when", None)
    body["kpis"].pop("today", None)
    return body


def test_the_same_runs_make_the_same_board_on_both_setups(tmp_path, monkeypatch):
    from backends import CloudHarness, LaptopHarness
    from eval_helpers import eval_config, load

    from foundry.engine.evaluate.evaluate import evaluate

    boards = []
    for h in (LaptopHarness(tmp_path / "laptop"), CloudHarness(tmp_path / "cloud", monkeypatch)):
        for name in ("clean", "recovered", "failed", "hostile"):
            h.seed(name)
        evaluate(h.runs_dir / "fixture-clean", load(eval_config(tmp_path / f"e-{h.name}")))
        (h.runs_dir / "broken").mkdir()
        (h.runs_dir / "broken" / "run.json").write_text("{nope")
        h.sync()
        boards.append(_board(h))
    laptop, cloud = boards
    assert [c["run_id"] for c in laptop["runs"]] == [c["run_id"] for c in cloud["runs"]]
    assert laptop == cloud


def test_a_laptop_run_whose_process_died_shows_interrupted(tmp_path):  # spec AC9
    import json

    from backends import LaptopHarness

    from foundry.services import jobs

    h = LaptopHarness(tmp_path)
    add_config(h, "slow", [{"tool": "sleep", "input": {"seconds": 30}}, {"text": "done"}])
    run_id = h.client.post("/api/runs", headers=h.headers(), json={"prompt_id": "adb-001", "config": "slow"}).json()["run_id"]
    (job,) = jobs.live(h.ws)
    for _ in range(400):  # until run.json exists and says running (a real process: bounded polling)
        f = h.runs_dir / run_id / "run.json"
        if f.is_file() and json.loads(f.read_text()).get("status") == "running":
            break
        __import__("time").sleep(0.05)
    else:
        raise AssertionError(f"run {run_id} never started")
    job.proc.kill()
    h.drain()
    card = next(c for c in h.client.get("/api/runs").json()["runs"] if c["run_id"] == run_id)
    assert (card["status"], card["headline"], card["column"]) == ("interrupted", "interrupted", "attention")
    assert card["reason"] == "Its worker stopped before finishing"
    assert h.client.get(f"/api/runs/{run_id}").json()["run"]["status"] == "interrupted"


def test_a_stored_card_from_other_code_never_breaks_the_board(tmp_path, monkeypatch):  # spec AC4
    import json

    from backends import CloudHarness

    from foundry.data.store import Update

    h = CloudHarness(tmp_path, monkeypatch)
    h.seed("clean")
    item = h.store.get_item("TREE", "run#fixture-clean")
    card = {**json.loads(item["card"]), "added_by_newer_code": 1}
    card.pop("footer")  # and one field older code didn't write
    h.store.write(Update("TREE", "run#fixture-clean", set={"card": json.dumps(card)}))
    r = h.client.get("/api/runs")
    assert r.status_code == 200, r.text
    (c,) = r.json()["runs"]
    assert "added_by_newer_code" not in c and "footer" not in c and c["column"] == "attention"


# --- re-indexing (backend-parity ADR-3, spec AC5) -------------------------------------------------------------------


def _reindex_setup(tmp_path, monkeypatch, *names):
    import json

    from backends import CloudHarness

    from foundry.data.store import Update

    h = CloudHarness(tmp_path, monkeypatch)
    for n in names:
        h.seed(n)

    def stale(run_id):  # a card written by older code: its headline is wrong and it lacks a field
        item = h.store.get_item("TREE", f"run#{run_id}")
        card = {**json.loads(item["card"]), "headline": "stale"}
        card.pop("reason", None)
        h.store.write(Update("TREE", f"run#{run_id}", set={"card": json.dumps(card, sort_keys=True)}))
    return h, stale


def test_reindex_rewrites_only_stale_cards_and_is_idempotent(tmp_path, monkeypatch):
    from foundry.services import workers

    h, stale = _reindex_setup(tmp_path, monkeypatch, "clean", "failed")
    stale("fixture-failed")
    versions = {k: h.store.get_item("TREE", f"run#{k}")["version"] for k in ("fixture-clean", "fixture-failed")}
    out = workers.reindex(h.wws, h.store, h.wmirror)
    assert out == {"runs": 2, "updated": 1, "skipped": 0, "busy": 0, "next": None}
    card = next(c for c in h.client.get("/api/runs").json()["runs"] if c["run_id"] == "fixture-failed")
    assert card["headline"] != "stale" and card["reason"] == "Hit the model-call limit (3)"
    assert workers.reindex(h.wws, h.store, h.wmirror)["updated"] == 0  # idempotent
    assert {k: h.store.get_item("TREE", f"run#{k}")["version"] for k in versions} == versions  # no new versions


def test_reindex_skips_live_runs_and_counts_a_run_that_moved(tmp_path, monkeypatch):
    import time

    from foundry.data.store import Put, Update
    from foundry.services import workers

    h, stale = _reindex_setup(tmp_path, monkeypatch, "clean", "failed", "recovered")
    for r in ("fixture-clean", "fixture-failed", "fixture-recovered"):
        stale(r)
    far = time.time() + 3600
    h.store.write(Put({"pk": "JOB", "sk": "a" * 16, "kind": "run", "run_id": "fixture-clean", "status": "running",
                       "deadline": far}))
    h.store.write(Put({"pk": "JOB", "sk": "b" * 16 + "#0", "kind": "batch_item", "parent": "b" * 16,
                       "run_id": "fixture-failed", "status": "running", "deadline": far}))  # a batch's item counts
    real = h.wmirror.pull

    def pull_then_someone_writes(tree, item=None):
        v = real(tree, item=item)
        if tree == "run#fixture-recovered":  # a worker pushes between the re-index's pull and its write
            h.store.write(Update("TREE", tree, set={"version": v + 1}))
        return v

    monkeypatch.setattr(h.wmirror, "pull", pull_then_someone_writes)
    assert workers.reindex(h.wws, h.store, h.wmirror) == {"runs": 3, "updated": 0, "skipped": 2, "busy": 1, "next": None}


def test_reindex_pages_by_its_time_budget(tmp_path, monkeypatch):
    from foundry.services import workers

    h, _ = _reindex_setup(tmp_path, monkeypatch, "clean", "failed", "recovered")
    ticks = iter(range(1000))
    clock = lambda: float(next(ticks))  # noqa: E731 (each call is a second later)
    pages, after = [], None
    while True:
        out = workers.reindex(h.wws, h.store, h.wmirror, after=after, budget_s=1.5, clock=clock)
        pages.append(out["runs"])
        after = out["next"]
        if after is None:
            break
    assert sum(pages) == 3 and len(pages) > 1


def test_worker_events_are_listed_once():
    """lambda_worker.EVENTS names every event kind it dispatches, and nothing else does dispatch."""
    from foundry import lambda_worker

    assert set(lambda_worker._events()) == set(lambda_worker.EVENTS)
    sent = (ROOT / "infra" / "foundry_cloud" / "batch_sm.py").read_text() + (ROOT / "scripts" / "cloud.sh").read_text() \
        + (ROOT / "scripts" / "cloud_ops.py").read_text()
    for kind in lambda_worker.EVENTS:
        assert f'"{kind}"' in sent or f"'{kind}'" in sent or f"\\\"{kind}\\\"" in sent, kind


# --- job kinds (backend-parity ADR-4, spec AC7) ---------------------------------------------------------------------

KIND_CALLS = {"start": 1, "reserve": 1, "of_kind": 1, "live": 1, "of_run": 1, "alive_of": 1}  # jobs.<fn>: kind arg index


def _kind_uses():
    """(file:line, kind literal, the call's keywords) for every jobs.<fn>(ws, "<kind>", ...) in services and api."""
    out = []
    for path in sorted((ROOT / "src" / "foundry").rglob("*.py")):
        if path.parent.name not in ("services", "api"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in KIND_CALLS
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "jobs"):
                i = KIND_CALLS[node.func.attr]
                if len(node.args) > i and isinstance(node.args[i], ast.Constant):
                    out.append((f"{path.name}:{node.lineno}", node.args[i].value,
                                {k.arg: ast.unparse(k.value) for k in node.keywords}, node.func.attr))
    return out


def test_every_job_kind_used_is_declared_once():
    from foundry.services import job_kinds

    uses = _kind_uses()
    assert len(uses) >= 5, uses  # the scan finds the real call sites
    unknown = [(where, k) for where, k, _, _ in uses if k not in job_kinds.KINDS]
    assert not unknown, unknown
    for where, k, kw, fn in uses:  # a start call agrees with the registry about one-at-a-time
        if fn == "start":
            assert (kw.get("singleton") == "True") == job_kinds.KINDS[k].singleton, (where, k, kw)
            assert job_kinds.KINDS[k].startable, (where, k)


def test_an_unknown_job_kind_is_refused_and_an_old_record_is_tolerated(tmp_path):
    import pytest

    from foundry.services import job_kinds, jobs
    from foundry.services.workspace import Workspace

    with pytest.raises(ValueError, match="unknown job kind"):
        jobs.start(Workspace(tmp_path, tmp_path, 0), "nope", "x", "x", [])
    with pytest.raises(ValueError, match="reservation only"):
        jobs.start(Workspace(tmp_path, tmp_path, 0), "draft", "x", "x", [])
    assert job_kinds.failed_visibly("retired-kind", 1, "done", alive=False, dismissed=False) is True
    assert job_kinds.failed_visibly("run", 1, "done", alive=False, dismissed=False) is False
    assert job_kinds.failed_visibly("run", None, "interrupted", alive=False, dismissed=False) is True


# --- one batch orchestrator (backend-parity ADR-5, spec AC6) --------------------------------------------------------


def test_the_same_batch_gives_the_same_results_on_both_setups(tmp_path, monkeypatch):
    import json

    from backends import CloudHarness, LaptopHarness

    from foundry.engine.batch import rows_from_csv

    results = []
    for h in (LaptopHarness(tmp_path / "laptop"), CloudHarness(tmp_path / "cloud", monkeypatch)):
        add_config(h, "ok", [{"tool": "echo", "input": {"text": "x"}}, {"text": "done"}])
        r = h.client.post("/api/batches", headers=h.headers(), json={
            "config": "ok", "prompts": ["adb-001", "adb-002"], "repeats": 1, "concurrency": 2, "evaluate": True,
            "max_cost": 1})
        assert r.status_code == 201, r.text
        batch_id = r.json()["batch_id"]
        h.drain(timeout=180)
        d = h.pull_batch(batch_id)
        meta = json.loads((d / "batch.json").read_text())
        for k in ("id", "started_at", "finished_at", "spent_usd", "runs_dir"):
            meta.pop(k)
        assert meta["configs"] == ["config/ok.toml"], meta["configs"]  # never a server's path
        rows = rows_from_csv(d / "summary.csv")
        for row in rows:
            for k in ("duration_s", "run_dir", "cost_usd"):
                row.pop(k)
        assert (d / "summary.md").is_file()  # the cloud writes it too now
        cards = {c["run_id"].removeprefix(f"{batch_id}_"): (c["column"], c["status"], c["headline"])
                 for c in h.client.get("/api/runs").json()["runs"] if c["run_id"].startswith(batch_id)}
        (job,) = [j for j in h.client.get("/api/jobs").json()["jobs"] if j["kind"] == "batch"]
        results.append((meta, rows, cards, job["exit_code"]))
    laptop, cloud = results
    assert laptop[2] and all(col == "scored" for col, _, _ in laptop[2].values())
    assert laptop == cloud


# --- every cloud read is reached by a test on the cloud setup (Verify pass 1: mutations that survived) -------------


def test_a_prompt_with_runs_keeps_its_text_and_counts_them(backend):
    """The text lock (increment 13) reads the task's runs: on the cloud they must be fetched first."""
    h = backend
    h.seed("clean")  # a run of adb-001
    r = h.client.put("/api/prompts/adb-001", headers=h.headers(), json={"task": "adb", "prompt": "edited"})
    assert r.status_code == 409, r.text
    row = next(p for p in h.client.get("/api/prompts").json()["prompts"] if p["prompt_id"] == "adb-001")
    assert row["runs"] == 1 and h.client.get("/api/prompts/adb-001").json()["runs_total"] == 1


def test_the_tool_menu_shows_the_cached_listing_and_traced_tools(backend):
    h = backend
    h.seed("clean")  # calls roda/search_datasets
    h.set_tool_cache({"listed_at": "2026-11-01T00:00:00Z", "servers": [], "skipped_configs": [],
                      "tools": [{"server": "stac", "name": "search_items", "description": "Scenes for an area"}]})
    tools = {(t["server"], t["name"]): t for t in h.client.get("/api/prompts/tools?task=adb").json()["tools"]}
    assert tools[("stac", "search_items")]["description"] == "Scenes for an area"
    assert tools[("roda", "search_datasets")]["runs"] == 1


def test_the_rubric_versions_count_the_runs_each_scored(backend, tmp_path):
    import json

    from eval_helpers import eval_config, load
    from test_api import RUBRIC

    from foundry.engine.evaluate.evaluate import evaluate

    h = backend
    d = h.seed("clean")
    evaluate(d, load(eval_config(tmp_path / "e")))  # scored by the original rubric
    h.sync()
    did = h.client.post("/api/rubric/drafts", headers=h.headers(), data={"task": "adb", "kind": "file", "config": "offline"},
                        files={"file": ("clay.json", json.dumps(RUBRIC).encode())}).json()["draft_id"]
    r = h.client.put(f"/api/rubric/drafts/{did}", headers=h.headers(), json={"task": "adb", "body": RUBRIC, "activate": True})
    assert r.json()["activated"] is True
    runs = {v["rubric_id"]: v["runs"] for v in h.client.get("/api/rubric?task=adb").json()["versions"]}
    assert runs == {"clay-adb": 0, "adb-rubric-v0": 1}


def test_importing_the_same_trace_twice_is_refused(backend):
    from test_api_ports import _hand_otlp

    h = backend
    files = {"file": ("trace.json", __import__("json").dumps(_hand_otlp()), "application/json")}
    form = {"task": "adb", "prompt_id": "ext-1", "config": ""}
    assert h.client.post("/api/import", headers=h.headers(), data=form, files=files).status_code == 201
    r = h.client.post("/api/import", headers=h.headers(), data=form, files=files)
    assert r.status_code == 409 and "Already imported" in r.json()["error"]


def test_a_run_finishing_while_the_board_is_read_is_not_a_crash(tmp_path, monkeypatch):  # Verify pass 1, race
    import json
    import subprocess

    from backends import LaptopHarness

    from foundry.services import jobs, state

    h = LaptopHarness(tmp_path)
    d = h.seed("clean", "2026-01-01T00-00-00Z_adb-001_aaaa")
    run = json.loads((d / "run.json").read_text())
    final, run["status"] = run["status"], "running"
    (d / "run.json").write_text(json.dumps(run))
    proc = subprocess.Popen(["sleep", "30"])
    job = jobs.Job("abcdabcdabcdabcd", "run", d.name, "x", proc, tmp_path / "log")
    h.ws.jobs[job.id] = job
    real = state.LocalState.board_rows

    def rows_then_finish(self, ws):  # run.json is read while running; then the run finishes normally
        out = real(self, ws)
        (d / "run.json").write_text(json.dumps({**run, "status": final}))
        job.exit_code = 0
        return out

    monkeypatch.setattr(state.LocalState, "board_rows", rows_then_finish)
    try:
        card = next(c for c in h.client.get("/api/runs").json()["runs"] if c["run_id"] == d.name)
        assert card["status"] == "running" and "reason" not in card
    finally:
        proc.kill()


# --- every project write reaches the store (Verify pass 2: a write that skipped project_write passed every test) ---


def test_every_prompt_write_is_read_back_by_a_fresh_request(backend):
    """Create, edit, expected steps, new version, archive: each is read back by the next request, which on the cloud
    starts from an empty copy, so a write that never left the instance (no project_write) fails here."""
    h = backend
    c, hd = h.client, h.headers()

    def prompt(pid):
        return c.get(f"/api/prompts/{pid}?task=adb").json()["prompt"]

    pid = c.get("/api/prompts?task=adb").json()["next_id"]
    r = c.post("/api/prompts", headers=hd, json={"task": "adb", "prompt_id": pid, "prompt": "Map wetlands in the Everglades."})
    assert r.status_code == 201, r.text
    assert prompt(pid)["prompt"] == "Map wetlands in the Everglades."
    assert pid in c.get("/api/session").json()["prompts"]  # the run form offers it, from the fetched project
    assert c.put(f"/api/prompts/{pid}", headers=hd, json={"task": "adb", "prompt": "Map wetlands, 2020-2024."}).status_code == 200
    assert prompt(pid)["prompt"] == "Map wetlands, 2020-2024."
    assert c.put(f"/api/prompts/{pid}/stages", headers=hd, json={"task": "adb", "stages": [["search_datasets"]]}).status_code == 200
    assert prompt(pid)["stages"] == [["search_datasets"]]
    v2 = c.post(f"/api/prompts/{pid}/version", headers=hd, json={"task": "adb"}).json()["prompt_id"]
    assert prompt(v2)["prompt"] == "Map wetlands, 2020-2024."
    assert c.post(f"/api/prompts/{pid}/archive", headers=hd, json={"task": "adb", "archived": True}).status_code == 200
    assert prompt(pid)["archived"] is True and pid not in c.get("/api/session").json()["prompts"]
    stale = c.get("/api/prompts?task=adb").json().get("base_version")
    if stale is not None:  # the cloud checks the version the page loaded (the laptop's files can't move under it)
        c.post(f"/api/prompts/{v2}/archive", headers=hd, json={"task": "adb", "archived": True})
        r = c.put(f"/api/prompts/{v2}", headers=hd, json={"task": "adb", "prompt": "late", "base_version": stale})
        assert r.status_code == 409 and "reload" in r.json()["error"]


STALE_WRITES = [  # every write route that takes the project version its page loaded (aws-deploy spec S2)
    ("post", "/api/prompts", {"task": "adb", "prompt_id": "adb-901", "prompt": "x"}),
    ("put", "/api/prompts/adb-002", {"task": "adb", "prompt": "x"}),
    ("put", "/api/prompts/adb-002/stages", {"task": "adb", "stages": [["search_datasets"]]}),
    ("post", "/api/prompts/adb-002/version", {"task": "adb"}),
    ("post", "/api/prompts/adb-002/archive", {"task": "adb", "archived": True}),
    ("put", "/api/rubric/drafts/{draft}", {"task": "adb", "body": {}}),
    ("post", "/api/rubric/history/activate", {"task": "adb", "file": "adb-rubric-v0@0.1.json"}),
]


@pytest.mark.cloud_only("a write from a page showing an older project version: a laptop's files can't change under it")
@pytest.mark.parametrize("method,path,body", STALE_WRITES, ids=[f"{m} {p}" for m, p, _ in STALE_WRITES])
def test_a_write_from_a_stale_page_is_refused(backend, method, path, body):
    import json

    from test_api import RUBRIC

    h = backend
    c, hd = h.client, h.headers()
    draft = c.post("/api/rubric/drafts", headers=hd, data={"task": "adb", "kind": "file", "config": "offline"},
                   files={"file": ("clay.json", json.dumps(RUBRIC).encode())}).json()["draft_id"]
    stale = c.get("/api/prompts?task=adb").json()["base_version"]
    nxt = c.get("/api/prompts?task=adb").json()["next_id"]
    assert c.post("/api/prompts", headers=hd, json={"task": "adb", "prompt_id": nxt, "prompt": "moved"}).status_code == 201
    r = getattr(c, method)(path.format(draft=draft), headers=hd, json={**body, "base_version": stale})
    assert r.status_code == 409 and "reload" in r.json()["error"], (r.status_code, r.text)
