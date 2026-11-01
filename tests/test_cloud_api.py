"""The cloud read and write paths end to end (aws-deploy slice 3c): two API instances and a worker share one fake
store; nothing but the store connects them."""


import pytest
from conftest import ROOT
from fakestore import FakeStore
from fastapi.testclient import TestClient
from test_cloud_jobs import fake_runner

from foundry import cloud_boot
from foundry.data.mirror import Mirror
from foundry.server import create_app
from foundry.services import jobs, workers
from foundry.services.cloud_jobs import CloudBackend
from foundry.services.workspace import Workspace

BASE = "http://127.0.0.1:8765"


def project(tmp_path, name):
    root = tmp_path / name / "project"
    root.mkdir(parents=True)
    (root / "config").symlink_to(ROOT / "config")
    (root / "fixtures").symlink_to(ROOT / "fixtures")
    (root / "tasks").mkdir()
    return root


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    store, invoked = FakeStore(), []
    cloud_boot.seed_projects(store, Mirror(store, tmp_path / "seed"), app=ROOT)

    def api(name):
        root = project(tmp_path, name)
        mirror = Mirror(store, root.parent)
        for it in store.query("TREE", prefix="project#"):
            mirror.pull(it["sk"], item=it)
        app = create_app(root.parent / "runs", root, cloud=(store, mirror, CloudBackend(store, invoke=invoked.append)))
        c = TestClient(app, base_url=BASE)
        return c, {"x-csrf-token": c.get("/api/session").json()["csrf"]}

    wroot = project(tmp_path, "worker")
    (wroot.parent / "runs").mkdir()
    wmirror = Mirror(store, wroot.parent)
    w = Workspace(wroot.parent / "runs", wroot, 0, backend=CloudBackend(store))
    w.cloud = (store, wmirror)
    monkeypatch.setattr(jobs, "run_foundry", fake_runner())
    return store, invoked, api("api-a"), api("api-b"), (w, wmirror)


def test_seed_is_once_and_complete(cloud):
    store, *_ = cloud
    v = store.get_item("TREE", "project#adb")["version"]
    cloud_boot.seed_projects(store, Mirror(store, ROOT / "never-written"), app=ROOT)  # an existing tree is left alone
    assert store.get_item("TREE", "project#adb")["version"] == v
    assert not (ROOT / "never-written").exists()


def test_a_run_started_on_one_instance_shows_on_another(cloud):
    store, invoked, (a, ha), (b, hb), (w, wm) = cloud
    r = a.post("/api/runs", headers=ha, json={"prompt_id": "adb-001", "config": "offline"})
    assert r.status_code == 201, r.text
    run_id = r.json()["run_id"]
    assert b.post("/api/runs", headers=hb, json={"prompt_id": "adb-001", "config": "offline"}).status_code == 409
    assert workers.run_job(w, store, wm, invoked[0]) == "done"
    board = b.get("/api/runs").json()
    (card,) = [c for c in board["runs"] if c["run_id"] == run_id]
    assert card["column"] in ("attention", "scored") and card["kpi"]["spent"] >= 0 and board["kpis"]["runs"] == 1
    d = b.get(f"/api/runs/{run_id}")
    assert d.status_code == 200 and d.json()["run"]["run_id"] == run_id and d.json()["m"]["rows"]
    dash = b.get("/api/dashboard?rubric=all").json()
    assert dash["total_runs"] == 1


def test_project_writes_reach_every_instance(cloud):
    store, invoked, (a, ha), (b, hb), _ = cloud
    before = {p["prompt_id"] for p in b.get("/api/prompts").json()["prompts"]}
    pid = b.get("/api/prompts").json()["next_id"]
    r = a.post("/api/prompts", headers=ha, json={"task": "adb", "prompt_id": pid, "prompt": "A new question about NAIP coverage."})
    assert r.status_code == 201, r.text
    pid = r.json()["prompt_id"]
    after = {p["prompt_id"] for p in b.get("/api/prompts").json()["prompts"]}
    assert after - before == {pid}
    assert b.put(f"/api/prompts/{pid}", headers=hb, json={"task": "adb", "prompt": "Edited on B."}).status_code == 200
    assert a.get(f"/api/prompts/{pid}").json()["prompt"]["prompt"] == "Edited on B."


def test_scoring_through_the_api_marks_the_card_and_clears_it(cloud):
    store, invoked, (a, ha), (b, hb), (w, wm) = cloud
    run_id = a.post("/api/runs", headers=ha, json={"prompt_id": "adb-001", "config": "offline"}).json()["run_id"]
    workers.run_job(w, store, wm, invoked[0])
    r = b.post(f"/api/runs/{run_id}/evaluate", headers=hb)
    assert r.status_code == 202, r.text
    card = next(c for c in a.get("/api/runs").json()["runs"] if c["run_id"] == run_id)
    assert card["headline"] == "evaluating" and card["column"] == "evaluating"
    assert a.post(f"/api/runs/{run_id}/evaluate", headers=ha).status_code == 409
    workers.run_job(w, store, wm, invoked[1])
    card = next(c for c in a.get("/api/runs").json()["runs"] if c["run_id"] == run_id)
    assert card["headline"] != "evaluating"
    jobs_list = b.get("/api/jobs").json()["jobs"]
    assert {j["kind"] for j in jobs_list} >= {"run", "evaluate"}
