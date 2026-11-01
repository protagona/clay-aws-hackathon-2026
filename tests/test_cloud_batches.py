"""Batches on Step Functions (aws-deploy slice 5, design ADR-7): Prepare → items → Finalize on the fake store."""

import pytest
from conftest import ROOT
from fakestore import FakeStore
from fastapi.testclient import TestClient
from test_cloud_api import project
from test_cloud_jobs import fake_runner

from foundry import cloud_boot
from foundry.data.mirror import Mirror
from foundry.server import create_app
from foundry.services import jobs, workers
from foundry.services.cloud_jobs import CloudBackend
from foundry.services.workspace import Workspace

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store, started, calls = FakeStore(), [], []
    cloud_boot.seed_projects(store, Mirror(store, tmp_path / "seed"), app=ROOT)
    root = project(tmp_path, "api")
    mirror = Mirror(store, root.parent)
    for it in store.query("TREE", prefix="project#"):
        mirror.pull(it["sk"], item=it)
    backend = CloudBackend(store, start_batch=lambda job_id: started.append(job_id) or f"arn:exec:{job_id}")
    app = create_app(root.parent / "runs", root, cloud=(store, mirror, backend))
    app.state.ui.cap_usd = 30.0
    c = TestClient(app, base_url=BASE)
    h = {"x-csrf-token": c.get("/api/session").json()["csrf"]}
    wroot = project(tmp_path, "worker")
    (wroot.parent / "runs").mkdir()
    w = Workspace(wroot.parent / "runs", wroot, 0, backend=CloudBackend(store))
    wm = Mirror(store, wroot.parent)
    w.cloud = (store, wm)
    runner = fake_runner()

    def counting(args, *a, **k):
        calls.append(args[0])
        return runner(args, *a, **k)
    monkeypatch.setattr(jobs, "run_foundry", counting)
    return store, started, calls, c, h, w, wm


def start(c, h, **over):
    body = {"config": "offline", "prompts": ["adb-001", "adb-002"], "repeats": 1, "concurrency": 2, "max_cost": 2,
            "evaluate": True, **over}
    r = c.post("/api/batches", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()["batch_id"]


def test_a_batch_runs_through_the_workflow_and_settles(setup):
    store, started, calls, c, h, w, wm = setup
    batch_id = start(c, h)
    (job_id,) = started
    job = store.get_item("JOB", job_id)
    assert job["execution_arn"] == f"arn:exec:{job_id}" and job["reservation"] > 0
    plan = workers.batch_prepare(w, store, wm, job_id)
    assert plan["concurrency"] == 2 and [i["prompt"] for i in plan["items"]] == ["adb-001", "adb-002"]
    results = [workers.batch_item(w, store, wm, item) for item in plan["items"]]
    assert [r["status"] for r in results] == ["ran", "ran"] and calls.count("run") == 2 and calls.count("evaluate") == 2
    assert workers.batch_item(w, store, wm, plan["items"][0])["status"] == "ran"  # a redelivery: reported, not re-run
    assert calls.count("run") == 2
    out = workers.batch_finalize(w, store, wm, job_id)
    assert out["status"] == "done"
    month = store.get_item("JOB", job_id)["month"]
    assert store.get_item("SPEND", month)["committed"] == pytest.approx(out["spent"])  # reservation replaced by actual
    view = c.get(f"/api/batches/{batch_id}").json()
    assert view["meta"]["status"] == "finished" and [r["status"] for r in view["rows"]] == ["succeeded", "succeeded"]
    assert not c.get("/api/batches").json()["running"]
    assert all(j["kind"] != "batch_item" for j in c.get("/api/jobs").json()["jobs"])  # item records stay internal


def test_the_batch_budget_skips_items_and_late_costs_still_count(setup):
    from foundry.data.store import Update

    store, started, calls, c, h, w, wm = setup
    start(c, h)
    (job_id,) = started
    plan = workers.batch_prepare(w, store, wm, job_id)
    store.write(Update("JOB", job_id, set={"spent": 5.0}))  # already over its $2 cap
    assert workers.batch_item(w, store, wm, plan["items"][0])["status"] == "skipped (budget)"
    assert calls.count("run") == 0
    store.write(Update("JOB", job_id, set={"spent": 0.0}))
    workers.batch_finalize(w, store, wm, job_id, error="Map failed")
    month = store.get_item("JOB", job_id)["month"]
    before = store.get_item("SPEND", month)["committed"]
    workers._book_item_cost(store, store.get_item("JOB", job_id), job_id, 0.25)  # an item finishing after Finalize
    assert store.get_item("SPEND", month)["committed"] == pytest.approx(before + 0.25)


def test_one_batch_at_a_time_and_the_slot_frees_at_finalize(setup):
    store, started, calls, c, h, w, wm = setup
    start(c, h)
    assert c.post("/api/batches", headers=h, json={"config": "offline", "prompts": ["adb-001"]}).status_code == 409
    workers.batch_finalize(w, store, wm, started[0], error="cancelled")
    start(c, h)


def test_a_reader_never_sees_a_finished_job_with_an_unfinished_batch(setup, monkeypatch):  # slice 10, found live
    from foundry.services import cloud_jobs

    store, started, calls, c, h, w, wm = setup
    batch_id = start(c, h)
    (job_id,) = started
    plan = workers.batch_prepare(w, store, wm, job_id)
    for item in plan["items"]:
        workers.batch_item(w, store, wm, item)
    seen, real = [], cloud_jobs.finish

    def finish(*a, **k):  # what a reader sees just before the job is marked finished
        seen.append(c.get(f"/api/batches/{batch_id}").json()["meta"]["status"])
        return real(*a, **k)

    monkeypatch.setattr(cloud_jobs, "finish", finish)
    workers.batch_finalize(w, store, wm, job_id)
    assert seen == ["finished"]
    assert c.get(f"/api/batches/{batch_id}").json()["meta"]["status"] == "finished"


def test_a_finalize_during_a_read_never_shows_a_stopped_batch(setup, monkeypatch):  # slice 10, found live
    from foundry.services import cloud_state

    store, started, calls, c, h, w, wm = setup
    batch_id = start(c, h)
    (job_id,) = started
    for item in workers.batch_prepare(w, store, wm, job_id)["items"]:
        workers.batch_item(w, store, wm, item)
    real, fired = cloud_state.pull_runs, []

    def finalize_meanwhile(ws):  # Finalize lands while the API is pulling the runs
        if not fired:
            fired.append(1)
            workers.batch_finalize(w, store, wm, job_id)
        return real(ws)

    monkeypatch.setattr(cloud_state, "pull_runs", finalize_meanwhile)
    view = c.get(f"/api/batches/{batch_id}").json()
    assert fired and view["meta"]["status"] != "stopped"
    assert view["running"] or view["meta"]["status"] == "finished"
    assert c.get(f"/api/batches/{batch_id}").json()["meta"]["status"] == "finished"
