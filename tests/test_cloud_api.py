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


def test_a_run_whose_worker_died_reads_as_interrupted(cloud, monkeypatch):
    import json

    from foundry.data.store import Update

    store, invoked, (a, ha), (b, hb), (w, wm) = cloud
    clean = fake_runner()

    def dies_mid_run(args, cwd, **kw):  # the last sync before the crash: run.json still says running
        code, err = clean(args, cwd, **kw)
        p = __import__("pathlib").Path(args[args.index("--runs-dir") + 1]) / args[args.index("--run-id") + 1] / "run.json"
        p.write_text(json.dumps({**json.loads(p.read_text()), "status": "running"}))
        return code, err

    monkeypatch.setattr(jobs, "run_foundry", dies_mid_run)
    run_id = a.post("/api/runs", headers=ha, json={"prompt_id": "adb-001", "config": "offline"}).json()["run_id"]
    workers.run_job(w, store, wm, invoked[0])
    store.write(Update("JOB", invoked[0], set={"status": "interrupted"}))  # what the sweep writes past the deadline
    card = next(c for c in b.get("/api/runs").json()["runs"] if c["run_id"] == run_id)
    assert card["headline"] == card["status"] == "interrupted" and card["column"] == "attention"
    assert card["reason"] == "Its worker stopped before finishing"  # board-attention B1
    d = b.get(f"/api/runs/{run_id}").json()
    assert d["run"]["status"] == "interrupted" and d["m"]["live"] is False


def test_the_receiver_is_off_and_uploads_may_go_to_the_bucket(cloud, tmp_path):
    _, _, (a, ha), _, _ = cloud
    s = a.get("/api/session")
    assert s.json()["receiver"] is False
    assert "connect-src 'self' https://fake-bucket.s3.us-west-2.amazonaws.com;" in s.headers["content-security-policy"]
    r = a.post("/api/receiver/start", headers=ha, json={"task": "adb", "prompt_id": "imported", "config": ""})
    assert r.status_code == 409 and r.json()["error"] == "Not available in this deployment; import trace files instead"
    local = TestClient(create_app(tmp_path / "runs", ROOT), base_url=BASE).get("/api/session")
    assert local.json()["receiver"] is True and "connect-src 'self';" in local.headers["content-security-policy"]


def test_cold_pulls_fetch_every_tree_in_parallel(tmp_path):  # slice 10: 26-34 s one tree at a time
    import threading

    from foundry.data.mirror import Mirror
    from foundry.services import cloud_state

    store = FakeStore()
    for i in range(20):
        Mirror(store, tmp_path / "w").push(f"run#r-{i:02d}", {"run.json": b'{"status": "succeeded"}'})
    mirror, threads = Mirror(store, tmp_path / "fresh"), set()
    real = mirror.pull

    def pull(tree, item=None):
        threads.add(threading.get_ident())
        return real(tree, item=item)

    mirror.pull = pull
    cloud_state.pull_all(mirror, store.query("TREE", prefix="run#"))
    assert sorted(p.name for p in (tmp_path / "fresh" / "runs").iterdir()) == [f"r-{i:02d}" for i in range(20)]
    assert len(threads) > 1


def test_seeding_instance_gets_the_files_too(tmp_path):
    """The instance that seeds pulls the files like any other (a worker that cold-starts first, 2026-11-03)."""
    store = FakeStore()
    mirror = Mirror(store, tmp_path / "state")
    cloud_boot.seed_projects(store, mirror, app=ROOT)
    for it in store.query("TREE", prefix="project#"):
        mirror.pull(it["sk"], item=it)
    assert (tmp_path / "state" / "project" / "tasks" / "adb" / "prompts.jsonl").is_file()
