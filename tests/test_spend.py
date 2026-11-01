"""The monthly spend cap (aws-deploy slice 4, spec A5, design ADR-8)."""

import pytest
from conftest import ROOT
from fakestore import FakeStore
from test_cloud_jobs import Clock, instance, run_args

from foundry.config import load_config
from foundry.services import cloud_jobs, jobs, spend
from foundry.services.errors import ServiceError


def test_run_reservation_comes_from_the_config():
    cfg = load_config(ROOT / "config" / "bedrock.toml")
    p, lim = cfg.prices[cfg.model.id], cfg.limits
    expected = (lim.max_input_tokens * p.input + lim.max_model_calls * lim.max_tokens * p.output) / 1e6 * 1.1
    assert spend.run_reservation(cfg, "bedrock") == pytest.approx(expected, abs=1e-4)
    assert spend.run_reservation(load_config(ROOT / "config" / "offline.toml"), "offline") == 0.0  # no paid calls
    assert spend.eval_reservation(cfg, "bedrock") > 0


def test_the_specs_worked_batch_example():
    assert spend.batch_reservation(2.0, 2, 1.87, 0.30) == pytest.approx(6.34)


def test_no_price_means_no_start_while_capped():
    cfg = load_config(ROOT / "config" / "bedrock.toml")
    with pytest.raises(ServiceError) as e:
        spend.run_reservation(cfg, "bedrock", model_id="us.example.unpriced-v1")
    assert e.value.status == 409 and "No price for us.example.unpriced-v1" in e.value.message


def capped(tmp_path, cap=5.0):
    store, clock = FakeStore(), Clock()
    a, _ = instance(tmp_path, "a", store, clock, [])
    b, _ = instance(tmp_path, "b", store, clock, [])
    a.cap_usd = b.cap_usd = cap
    return store, clock, a, b


def committed(store, clock):
    return (store.get_item("SPEND", spend.month(clock.t)) or {}).get("committed", 0)


def test_reservations_refuse_past_the_cap_across_instances(tmp_path):
    store, clock, a, b = capped(tmp_path)
    j1 = jobs.start(a, "run", "r-1", "Run", run_args(a, "r-1"), reservation=2.0)
    j2 = jobs.start(b, "evaluate", "r-0", "Eval", ["evaluate"], reservation=2.5)
    with pytest.raises(ServiceError) as e:
        jobs.start(a, "run", "r-2", "Run", run_args(a, "r-2"), reservation=1.0)  # 4.5 + 1.0 > 5
    assert e.value.status == 409 and "$4.50 of the $5.00 cap" in e.value.message and "paused until" in e.value.message
    assert cloud_jobs.finish(store, j1.id, "done", 0, None, clock.t, actual=0.4)
    assert committed(store, clock) == pytest.approx(2.9)  # 0.4 spent + 2.5 still reserved
    jobs.start(a, "run", "r-2", "Run", run_args(a, "r-2"), reservation=1.0)
    assert cloud_jobs.finish(store, j2.id, "failed", 1, "boom", clock.t)  # unknown cost: charged the reservation
    assert committed(store, clock) == pytest.approx(3.9)


def test_a_reservation_larger_than_the_cap_is_refused_up_front(tmp_path):
    store, clock, a, _ = capped(tmp_path, cap=1.0)
    with pytest.raises(ServiceError):
        jobs.start(a, "batch", "b-1", "Batch", ["batch"], singleton=True, reservation=6.34)
    assert store.query("JOB") == [] and store.get_item("SLOT", "batch") is None


def test_settles_on_the_start_month_and_exactly_once(tmp_path):
    store, clock, a, _ = capped(tmp_path)
    import datetime as dt

    clock.t = dt.datetime(2027, 1, 31, 23, 59, 50, tzinfo=dt.timezone.utc).timestamp()  # 10 s before February
    job = jobs.start(a, "run", "r-1", "Run", run_args(a, "r-1"), reservation=2.0)
    clock.t += 60  # February now
    assert cloud_jobs.finish(store, job.id, "done", 0, None, clock.t, actual=0.5)
    assert store.get_item("SPEND", "2027-01")["committed"] == pytest.approx(0.5)
    assert store.get_item("SPEND", "2027-02") is None  # no negative headroom in the new month
    assert not cloud_jobs.finish(store, job.id, "interrupted", None, None, clock.t)  # a late sweep books nothing
    assert store.get_item("SPEND", "2027-01")["committed"] == pytest.approx(0.5)


def test_the_sweep_charges_the_reservation(tmp_path):
    store, clock, a, b = capped(tmp_path)
    start_month = spend.month(clock.t)
    jobs.start(a, "run", "r-1", "Run", run_args(a, "r-1"), reservation=2.0)
    clock.t += cloud_jobs.EVENT_AGE_S + cloud_jobs.RUN_LIMIT_S + cloud_jobs.MARGIN_S + 1
    assert jobs.live(b) == []  # reading sweeps it
    assert store.get_item("SPEND", start_month)["committed"] == pytest.approx(2.0)  # charged its reservation
    (job,) = store.query("JOB")
    assert job["status"] == "interrupted" and job["charged"] == pytest.approx(2.0)


def test_local_cap_with_a_ledger(tmp_path, monkeypatch):
    from foundry.services.workspace import Workspace

    class Done:
        alive, exit_code, kind, run_id, prompts = False, 0, "run", "r", None

        def __init__(self):
            self.id = "x" * 16

    monkeypatch.setattr(jobs, "spawn", lambda *a, **k: Done())
    (tmp_path / "runs").mkdir()
    ws = Workspace(tmp_path / "runs", ROOT, 0, cap_usd=3.0)
    jobs.start(ws, "run", "r-1", "Run", ["run"], reservation=2.0)
    with pytest.raises(ServiceError) as e:
        jobs.start(ws, "run", "r-2", "Run", ["run"], reservation=2.0)
    assert "$2.00 of the $3.00 cap" in e.value.message
    ws2 = Workspace(tmp_path / "runs", ROOT, 0)  # no cap: nothing checked
    jobs.start(ws2, "run", "r-3", "Run", ["run"], reservation=2.0)
