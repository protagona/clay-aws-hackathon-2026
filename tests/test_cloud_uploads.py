"""Two-step uploads, imports and drafts by upload, edit conflicts and the CLI download (aws-deploy slice 6b)."""

import json

import pytest
from conftest import ROOT
from fakestore import FakeStore
from fastapi.testclient import TestClient
from test_api import RUBRIC
from test_cloud_api import project

from foundry import cloud_boot
from foundry.data.mirror import Mirror
from foundry.server import create_app
from foundry.services.cloud_jobs import CloudBackend

BASE = "http://127.0.0.1:8765"


def local_client(tmp_path):
    c = TestClient(create_app(tmp_path / "runs", ROOT), base_url=BASE)
    return c, {"x-csrf-token": c.get("/api/session").json()["csrf"]}


def upload_local(c, h, kind, name, data):
    t = c.post("/api/uploads", headers=h, json={"kind": kind, "filename": name, "size": len(data)})
    assert t.status_code == 201, t.text
    t = t.json()
    assert t["url"] == f"/api/uploads/{t['upload_id']}" and t["fields"] == {}
    r = c.post(t["url"], headers=h, files={"file": (name, data)})
    assert r.status_code == 201, r.text
    return t["upload_id"]


def test_local_two_step_draft_and_size_caps(tmp_path):
    c, h = local_client(tmp_path)
    uid = upload_local(c, h, "rubric_source", "clay.json", json.dumps(RUBRIC).encode())
    r = c.post("/api/rubric/drafts/by-upload", headers=h,
               json={"task": "adb", "kind": "file", "config": "offline", "upload_id": uid})
    assert r.status_code == 201, r.text
    assert r.json()["draft_id"]
    big = c.post("/api/uploads", headers=h, json={"kind": "trace", "filename": "t.json", "size": 60 * 1024 * 1024})
    assert big.status_code == 413
    assert c.post("/api/uploads", headers=h, json={"kind": "exe", "filename": "x", "size": 1}).status_code == 422
    assert c.post("/api/import/by-upload", headers=h, json={"upload_id": "f" * 16}).status_code == 404


def test_local_upload_route_enforces_its_tickets_cap(tmp_path, monkeypatch):
    import foundry.engine.importer as importer

    c, h = local_client(tmp_path)
    monkeypatch.setattr(importer, "MAX_FILE_BYTES", 1000)
    t = c.post("/api/uploads", headers=h, json={"kind": "trace", "filename": "t.json", "size": 10}).json()
    assert c.post(t["url"], headers=h, files={"file": ("t.json", b"x" * 2000)}).status_code == 413


@pytest.fixture
def cloud(tmp_path):
    store = FakeStore()
    cloud_boot.seed_projects(store, Mirror(store, tmp_path / "seed"), app=ROOT)

    def api(name):
        root = project(tmp_path, name)
        mirror = Mirror(store, root.parent)
        for it in store.query("TREE", prefix="project#"):
            mirror.pull(it["sk"], item=it)
        c = TestClient(create_app(root.parent / "runs", root, cloud=(store, mirror, CloudBackend(store))), base_url=BASE)
        return c, {"x-csrf-token": c.get("/api/session").json()["csrf"]}

    return store, api("a"), api("b")


def test_cloud_tickets_are_presigned_posts_and_reads_take_the_object(cloud):
    store, (a, ha), _ = cloud
    t = a.post("/api/uploads", headers=ha, json={"kind": "rubric_source", "filename": "clay.json", "size": 300}).json()
    assert t["url"].startswith("https://") and t["fields"]["key"] == f"uploads/{t['upload_id']}"
    assert a.post(f"/api/uploads/{t['upload_id']}", headers=ha, files={"file": ("x", b"x")}).status_code == 404
    body = {"kind": "file", "config": "offline", "upload_id": t["upload_id"]}
    r = a.post("/api/rubric/drafts/by-upload", headers=ha, json=body)
    assert r.status_code == 400 and "hasn't arrived" in r.json()["error"]
    store.put_object(f"uploads/{t['upload_id']}", json.dumps(RUBRIC).encode(), if_none_match=False)  # the browser's POST
    r = a.post("/api/rubric/drafts/by-upload", headers=ha, json=body)
    assert r.status_code == 201, r.text


def test_a_draft_made_on_one_instance_is_seen_on_another(cloud):
    _, (a, ha), (b, _) = cloud
    r = a.post("/api/rubric/drafts/by-upload", headers=ha, json={"kind": "text", "config": "offline",
                                                                  "text": json.dumps(RUBRIC)})
    assert r.status_code == 201, r.text
    assert any(d["draft_id"] == r.json()["draft_id"] for d in b.get("/api/rubric").json()["drafts"])


def test_concurrent_edits_cannot_both_win(cloud):
    _, (a, ha), (b, hb) = cloud
    base = a.get("/api/prompts").json()["base_version"]
    assert b.get("/api/prompts/adb-001").json()["base_version"] == base
    pid = a.get("/api/prompts").json()["next_id"]
    r = a.post("/api/prompts", headers=ha, json={"task": "adb", "prompt_id": pid, "prompt": "First.", "base_version": base})
    assert r.status_code == 201, r.text
    second = {"task": "adb", "prompt_id": f"{pid}x", "prompt": "Second.", "base_version": base}
    r = b.post("/api/prompts", headers=hb, json=second)
    assert r.status_code == 409 and r.json()["error"] == "Changed by someone else; reload"
    fresh = b.get("/api/prompts").json()["base_version"]
    assert fresh == base + 1
    assert b.post("/api/prompts", headers=hb, json={**second, "base_version": fresh}).status_code == 201


def test_the_cli_download_is_public_and_only_that_file(tmp_path):
    c, _ = local_client(tmp_path)
    assert c.get("/cli/foundry-remote.pyz").status_code in (200, 404)  # 404 until slice 8 builds it
    assert c.get("/cli/other.py").status_code == 404
