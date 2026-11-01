"""scripts/cloud_ops.py, the table and bucket side of cloud.sh (aws-deploy slice 9), on the fake store."""

import shutil
import sys

from conftest import ROOT
from fakestore import FakeStore
from fastapi.testclient import TestClient
from test_cloud_api import project

from foundry.data.mirror import Mirror
from foundry.data.tokens import TokenStore
from foundry.server import create_app
from foundry.services.access import Access
from foundry.services.cloud_jobs import CloudBackend

sys.path.insert(0, str(ROOT / "scripts"))
import cloud_ops  # noqa: E402


def source(tmp_path):
    src = tmp_path / "src"
    for name in ("clean", "golden"):
        shutil.copytree(ROOT / "fixtures" / "runs" / name, src / "runs" / f"2026-10-30T00-00-00Z_adb-001_{name[:4]}")
    shutil.copytree(ROOT / "tasks", src / "tasks", ignore=shutil.ignore_patterns("drafts"))
    (src / "runs" / ".spend.jsonl").write_text("{}\n")  # local ledger: never seeded
    return src


def test_seed_fills_an_empty_deployment_once_and_the_board_shows_it(tmp_path):
    store, logs = FakeStore(), []
    assert cloud_ops.seed(store, source(tmp_path), log=logs.append) == 2
    assert not any(k.endswith(".spend.jsonl") for k in store.current_keys())
    root = project(tmp_path, "api")
    mirror = Mirror(store, root.parent)
    for it in store.query("TREE", prefix="project#"):
        mirror.pull(it["sk"], item=it)
    c = TestClient(create_app(root.parent / "runs", root, cloud=(store, mirror, CloudBackend(store))),
                   base_url="http://127.0.0.1:8765")
    assert {r["run_id"][-4:] for r in c.get("/api/runs").json()["runs"]} == {"clea", "gold"}
    assert cloud_ops.seed(store, source(tmp_path / "again"), log=logs.append) == 0
    assert "skipped as a whole" in logs[-1]


def test_tokens_sign_in_and_revoke(tmp_path):
    store = FakeStore()
    assert cloud_ops.session_key(store) is True and cloud_ops.session_key(store) is False
    token = cloud_ops.token_add(store, "ana")
    assert token.startswith("fdy_") and [t["name"] for t in cloud_ops.token_list(store)] == ["ana"]
    assert store.get_item("TOKEN", "ana")["sha256"] != token  # only the hash is stored
    access = Access(TokenStore(store))
    assert access.token_name(token) == "ana"
    assert cloud_ops.token_revoke(store, "ana") is True and cloud_ops.token_revoke(store, "ana") is False
    assert Access(TokenStore(store)).token_name(token) is None


def test_gc_lists_only_old_unreferenced_objects(tmp_path):
    store = FakeStore()
    cloud_ops.seed(store, source(tmp_path), log=lambda *_: None)
    live = store.current_keys("runs/")
    listing = [(k, 0.0) for k in live] + [("runs/x/orphan@v1.n", 0.0), ("runs/x/fresh@v1.n", 10_000.0),
                                          ("uploads/abc", 0.0)]
    assert cloud_ops.unreferenced(store, listing, now=10_500.0) == ["runs/x/orphan@v1.n"]
    store.write(cloud_ops.Put({"pk": "JOB", "sk": "j", "status": "running", "deadline": 2e9}))
    assert cloud_ops.live_jobs(store, now=1e9) == 1 and cloud_ops.live_jobs(store, now=3e9) == 0
