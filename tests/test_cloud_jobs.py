"""Cloud jobs and workers (aws-deploy slice 3b, design ADR-6): two API instances and a worker on one fake store."""

import shutil

import pytest
from conftest import FIXTURES, ROOT
from fakestore import FakeStore

from foundry.data.mirror import Mirror
from foundry.services import cloud_jobs, jobs, workers
from foundry.services.cloud_jobs import CloudBackend
from foundry.services.errors import ServiceError
from foundry.services.workspace import Workspace


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def instance(tmp_path, name, store, clock, invoked=None):
    root = tmp_path / name
    (root / "runs").mkdir(parents=True)
    ws = Workspace(root / "runs", ROOT, 0, backend=CloudBackend(store, invoke=(invoked.append if invoked is not None else None),
                                                                clock=clock))
    return ws, Mirror(store, root)


@pytest.fixture
def cloud(tmp_path):
    store, clock, invoked = FakeStore(), Clock(), []
    a, _ = instance(tmp_path, "api-a", store, clock, invoked)
    b, _ = instance(tmp_path, "api-b", store, clock, invoked)
    w, wm = instance(tmp_path, "worker", store, clock)
    return store, clock, invoked, a, b, w, wm


def run_args(ws, run_id):
    return ["run", "--config", "config/offline.toml", "--prompt", "adb-001", "--runs-dir", str(ws.runs_dir),
            "--run-id", run_id]


def fake_runner(fixture="clean", code=0, err=""):
    """Stands in for `foundry run|evaluate`: materializes a fixture run under --runs-dir (or the run path)."""
    def run(args, cwd, env=None, tick=None, every=5.0, timeout=None):
        if args[0] == "run":
            dest = __import__("pathlib").Path(args[args.index("--runs-dir") + 1]) / args[args.index("--run-id") + 1]
            shutil.copytree(FIXTURES / fixture, dest)
        else:  # evaluate <path>: a scored fixture's scores and report
            dest = __import__("pathlib").Path(args[1])
            for f in ("scores.json", "report.md"):
                if (FIXTURES / "scored" / f).exists():
                    shutil.copy(FIXTURES / "scored" / f, dest / f)
        if tick:
            tick()
        return code, err
    return run


def test_one_ui_run_at_a_time_across_instances(cloud):
    store, clock, invoked, a, b, *_ = cloud
    jobs.start(a, "run", "r-1", "Run adb-001", run_args(a, "r-1"), singleton=True, prompt_ids=["adb-001"], task="adb")
    with pytest.raises(ServiceError) as e:
        jobs.start(b, "run", "r-2", "Run adb-001", run_args(b, "r-2"), singleton=True, task="adb")
    assert e.value.status == 409 and "A run is already in progress: r-1" in e.value.message
    assert len(invoked) == 1 and jobs.pending_prompt(b, "adb-001") is False  # run ids carry the prompt id
    assert [j.run_id for j in jobs.live(b, "run")] == ["r-1"]


def test_duplicate_delivery_runs_once_and_releases_the_slot(cloud, monkeypatch):
    store, clock, invoked, a, b, w, wm = cloud
    monkeypatch.setattr(jobs, "run_foundry", fake_runner())
    job = jobs.start(a, "run", "r-1", "Run", run_args(w, "r-1"), singleton=True, task="adb")
    assert workers.run_job(w, store, wm, job.id, clock) == "done"
    assert workers.run_job(w, store, wm, job.id, clock) == "duplicate"
    rec = jobs.find(b, job.id)
    assert rec.status == "done" and not rec.alive and rec.read_log() == ""
    jobs.start(b, "run", "r-2", "Run", run_args(b, "r-2"), singleton=True, task="adb")  # the slot is free again


def test_the_worker_pushes_the_run_and_its_index_card(cloud, monkeypatch):
    from foundry.data.index import run_items

    store, clock, invoked, a, b, w, wm = cloud
    monkeypatch.setattr(jobs, "run_foundry", fake_runner())
    job = jobs.start(a, "run", "r-1", "Run", run_args(w, "r-1"), singleton=True, task="adb")
    workers.run_job(w, store, wm, job.id, clock)
    (item,) = run_items(store)
    assert item["run_id"] == "r-1" and item["card"]["run_id"] == "r-1" and item["summary"]["prompt_id"]
    reader = Mirror(store, b.runs_dir.parent)
    reader.pull("run#r-1")
    assert (b.runs_dir / "r-1" / "run.json").is_file() and (b.runs_dir / "r-1" / "trace.jsonl").is_file()


def test_evaluation_lock_spans_instances_and_is_released(cloud, monkeypatch):
    store, clock, invoked, a, b, w, wm = cloud
    monkeypatch.setattr(jobs, "run_foundry", fake_runner())
    r = jobs.start(a, "run", "r-1", "Run", run_args(w, "r-1"), singleton=True, task="adb")
    workers.run_job(w, store, wm, r.id, clock)
    path = a.runs_dir / "r-1"
    e1 = jobs.start(a, "evaluate", "r-1", "Eval", ["evaluate", str(w.runs_dir / "r-1"), "--config", "config/offline.toml"],
                    evaluate_dir=path)
    assert jobs.is_evaluating(b, b.runs_dir / "r-1")
    with pytest.raises(ServiceError) as e:
        jobs.start(b, "evaluate", "r-1", "Eval", ["evaluate"], evaluate_dir=b.runs_dir / "r-1")
    assert e.value.status == 409 and "already being scored" in e.value.message
    workers.run_job(w, store, wm, e1.id, clock)
    assert not jobs.is_evaluating(b, b.runs_dir / "r-1")
    from foundry.data.index import run_items
    assert run_items(store)[0]["card"]["headline"] != "evaluating"  # re-indexed after the lock was released


def test_a_prompt_edit_holds_off_run_starts(cloud):
    store, clock, invoked, a, b, *_ = cloud
    with jobs.exclusive(b, "adb"):
        with pytest.raises(ServiceError) as e:
            jobs.start(a, "run", "r-1", "Run", run_args(a, "r-1"), singleton=True, task="adb")
        assert "being edited" in e.value.message
    jobs.start(a, "run", "r-1", "Run", run_args(a, "r-1"), singleton=True, task="adb")


def test_sweep_interrupts_once_and_frees_the_slot(cloud, monkeypatch):
    store, clock, invoked, a, b, w, wm = cloud
    job = jobs.start(a, "run", "r-1", "Run", run_args(w, "r-1"), singleton=True, task="adb")
    clock.t += cloud_jobs.EVENT_AGE_S + cloud_jobs.RUN_LIMIT_S + cloud_jobs.MARGIN_S + 1
    rec = jobs.find(b, job.id)
    assert rec.status == "interrupted" and rec.failed_visibly and "interrupted" in rec.last_stderr_line()
    assert cloud_jobs.finish(store, job.id, "done", 0, None, clock.t) is False  # a late worker can't finish it again
    jobs.start(b, "run", "r-2", "Run", run_args(b, "r-2"), singleton=True, task="adb")


def test_a_failed_invoke_finishes_the_job_and_says_so(cloud):
    store, clock, invoked, a, *_ = cloud

    def boom(job_id):
        raise RuntimeError("throttled")
    a.backend.invoke = boom
    with pytest.raises(ServiceError) as e:
        jobs.start(a, "run", "r-1", "Run", run_args(a, "r-1"), singleton=True, task="adb")
    assert e.value.status == 500
    (item,) = store.query("JOB")
    assert item["status"] == "failed" and store.get_item("SLOT", "run") is None


def test_failed_job_records_its_last_stderr_line(cloud, monkeypatch):
    store, clock, invoked, a, b, w, wm = cloud
    monkeypatch.setattr(jobs, "run_foundry", fake_runner(code=2, err="booting\nerror: no such prompt\n"))
    job = jobs.start(a, "tools", "tools", "Tool listing", ["tools", "--out", "x"], singleton=True)
    assert workers.run_job(w, store, wm, job.id, clock) == "failed"
    rec = jobs.find(b, job.id)
    assert rec.failed_visibly and rec.last_stderr_line() == "error: no such prompt" and "booting" in rec.read_log()
    jobs.dismiss(b, job.id)
    assert jobs.find(a, job.id).dismissed


def test_a_started_job_that_dies_frees_its_slot_17_minutes_after_it_started(cloud):  # S3 (Verify pass 1)
    store, clock, invoked, a, b, w, wm = cloud
    job = jobs.start(a, "run", "r-1", "Run", run_args(w, "r-1"), singleton=True, task="adb")
    clock.t += 60  # it waited a minute in Lambda's queue, then a worker claimed it and died
    workers.claim(store, job.id, clock.t, deadline=clock.t + cloud_jobs.RUN_LIMIT_S + cloud_jobs.MARGIN_S)
    clock.t += cloud_jobs.RUN_LIMIT_S + cloud_jobs.MARGIN_S - 1
    with pytest.raises(ServiceError):  # still within its 17 minutes: the slot is held
        jobs.start(b, "run", "r-2", "Run", run_args(b, "r-2"), singleton=True, task="adb")
    clock.t += 2  # 17 minutes after it started: a start alone frees it (no job-list read first, as from the CLI)
    jobs.start(b, "run", "r-2", "Run", run_args(b, "r-2"), singleton=True, task="adb")
    assert jobs.find(b, job.id).status == "interrupted"  # the next job-list read marks the dead job
