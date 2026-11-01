"""The mirror's guarantees (aws-deploy slice 2, design ADR-5), on the fake store with real semantics."""

import json
import time

import pytest
from fakestore import FakeStore

from foundry.data.mirror import Mirror, prefix
from foundry.data.store import Conflict, Cond, Put


def two(tmp_path):
    store = FakeStore()
    return store, Mirror(store, tmp_path / "a"), Mirror(store, tmp_path / "b")


def test_prefixes():
    assert prefix("run#r1") == "runs/r1/" and prefix("batch#b1") == "runs/.batches/b1/"
    assert prefix("project#adb") == "project/tasks/adb/" and prefix("file#tools") == "files/"
    with pytest.raises(ValueError):
        prefix("other#x")


def test_push_then_pull_elsewhere(tmp_path):
    store, a, b = two(tmp_path)
    assert a.push("run#r1", {"run.json": b'{"x":1}', "trace.jsonl": b"1\n"}) == 1
    assert b.pull("run#r1") == 1
    assert (tmp_path / "b/runs/r1/run.json").read_bytes() == b'{"x":1}'
    assert store.calls["get_object"] == 2
    assert b.pull("run#r1") == 1 and store.calls["get_object"] == 2  # unchanged version: no GETs


def test_pull_fetches_only_changes_and_propagates_deletes(tmp_path):
    store, a, b = two(tmp_path)
    a.push("project#adb", {"prompts.jsonl": b"p1", "rubrics/drafts/d1/meta.json": b"{}"})
    b.pull("project#adb")
    gets = store.calls["get_object"]
    a.push("project#adb", {"prompts.jsonl": b"p2"}, deleted={"rubrics/drafts/d1/meta.json"})  # a discarded draft
    b.pull("project#adb")
    assert store.calls["get_object"] == gets + 1  # only the changed file
    assert (tmp_path / "b/project/tasks/adb/prompts.jsonl").read_bytes() == b"p2"
    assert not (tmp_path / "b/project/tasks/adb/rubrics/drafts/d1/meta.json").exists()  # the delete arrived


def test_a_losing_push_never_overwrites_the_winner(tmp_path):
    store, a, b = two(tmp_path)
    a.push("project#adb", {"prompts.jsonl": b"base"})
    b.pull("project#adb")
    a.push("project#adb", {"prompts.jsonl": b"from-a"})       # a wins version 2
    with pytest.raises(Conflict):
        b.push("project#adb", {"prompts.jsonl": b"from-b"})   # b still thinks version 1
    fresh = Mirror(store, tmp_path / "c")
    fresh.pull("project#adb")
    assert (tmp_path / "c/project/tasks/adb/prompts.jsonl").read_bytes() == b"from-a"
    assert not any("from-b" in k for k in store.current_keys())  # the loser's object is deleted
    assert all(k.count("@v") == 1 for k in store.current_keys())


def test_superseded_objects_are_deleted_but_stay_readable_by_version(tmp_path):
    store, a, b = two(tmp_path)
    a.push("run#r1", {"trace.jsonl": b"1\n"})
    v1 = json.loads(store.get_item("TREE", "run#r1")["manifest"])["trace.jsonl"]
    a.push("run#r1", {"trace.jsonl": b"1\n2\n"})
    assert store.current_keys("runs/r1/") == [json.loads(store.get_item("TREE", "run#r1")["manifest"])["trace.jsonl"]["key"]]
    assert store.get_object(v1["key"], v1["vid"]) == b"1\n"  # a reader holding the old manifest still gets its bytes


def test_ambiguous_commit_that_applied_keeps_its_files(tmp_path):
    store, a, _ = two(tmp_path)
    a.push("project#adb", {"prompts.jsonl": b"v1"})
    store.fail_next_transact_after_apply = True  # the write applied, then the network failed
    assert a.push("project#adb", {"prompts.jsonl": b"v2"}) == 2  # it re-read, found its nonce: committed
    fresh = Mirror(store, tmp_path / "c")
    fresh.pull("project#adb")
    assert (tmp_path / "c/project/tasks/adb/prompts.jsonl").read_bytes() == b"v2"


def test_an_expired_or_foreign_lease_fences_the_push(tmp_path):
    store, a, _ = two(tmp_path)
    store.write(Put({"pk": "LOCK", "sk": "lease#project#adb", "owner": "someone-else", "expires": int(time.time()) + 60}))
    with pytest.raises(Conflict):
        a.push("project#adb", {"prompts.jsonl": b"x"}, lease=("lease#project#adb", "me"))
    assert store.get_item("TREE", "project#adb") is None and not store.current_keys()
    store.write(Put({"pk": "LOCK", "sk": "lease#project#adb", "owner": "me", "expires": int(time.time()) + 60}))
    assert a.push("project#adb", {"prompts.jsonl": b"x"}, lease=("lease#project#adb", "me")) == 1


def test_push_dir_skips_dotfiles_and_unchanged_files(tmp_path):
    store, a, b = two(tmp_path)
    run = a.local_dir("run#r1")
    run.mkdir(parents=True)
    (run / "run.json").write_text("{}")
    (run / ".run.json.tmp").write_text("partial")   # the engine's atomic-write temporary
    (run / ".evaluating").write_text("lock")
    assert a.push_dir("run#r1") == 1
    assert a.push_dir("run#r1") is None             # nothing changed
    (run / "trace.jsonl").write_text("1\n")
    assert a.push_dir("run#r1", attrs={"card": json.dumps({"headline": "running"})}) == 2
    b.pull("run#r1")
    assert sorted(p.name for p in b.local_dir("run#r1").iterdir()) == ["run.json", "trace.jsonl"]
    assert json.loads(store.get_item("TREE", "run#r1")["card"]) == {"headline": "running"}


def test_conditions_compile_like_they_evaluate():
    from foundry.data.store import _Names, compile_cond

    n = _Names()
    expr = compile_cond(Cond.any(Cond.absent(), Cond.lt("expires", 5)), n)
    assert expr == "(attribute_not_exists(pk) OR #n0 < :v0)" and n.names == {"#n0": "expires"}
    assert n.values == {":v0": {"N": "5"}}
    n = _Names()
    assert compile_cond(Cond.all(Cond.eq("owner", "me"), Cond.isin("status", ["queued", "running"])), n) == \
        "(#n0 = :v0 AND #n1 IN (:v1, :v2))"


def test_run_index_reads_cards_without_manifests(tmp_path):
    from foundry.data.index import run_attrs, run_items

    store, a, _ = two(tmp_path)
    a.push("run#r1", {"run.json": b"{}"}, attrs=run_attrs({"headline": "0.80"}, {"overall": 0.8, "spent": 0.1}))
    a.push("run#r2", {"run.json": b"{}"}, attrs=run_attrs({"headline": "running"}, {"overall": None, "spent": 0.0}))
    a.push("project#adb", {"prompts.jsonl": b"p"})
    items = run_items(store)
    assert [i["run_id"] for i in items] == ["r1", "r2"] and items[0]["summary"]["overall"] == 0.8
    assert all("manifest" not in i for i in items) and items[1]["card"] == {"headline": "running"}
