"""Increment 15, T1: `foundry tools` lists every tool of the configs' MCP servers, with descriptions."""

import re
import shutil
from pathlib import Path

from foundry.engine.harness.tools import list_tools

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path, name: str, servers: str) -> Path:
    """The offline config with its [mcp.servers.*] tables replaced (no network: only in-repo or bogus servers)."""
    text = (ROOT / "config/offline.toml").read_text()
    text = re.sub(r"\[mcp\.servers\.roda\].*?(?=\n\[mcp\.servers\.stac\])", "", text, flags=re.S)
    text = re.sub(r"\[mcp\.servers\.stac\].*?(?=\n\[tracing\])", servers, text, flags=re.S)
    path = tmp_path / f"{name}.toml"
    path.write_text(text)
    return path


STAC = '''[mcp.servers.stac]
command = "{python}"
args = ["-m", "foundry.engine.mcp_servers.stac"]
package = "foundry.mcp_servers.stac"
version = "0.1.0"
error_detection = "mcp"
env = { STAC_API_URL = "https://earth-search.aws.element84.com/v1" }
'''


def test_list_tools_from_stac(tmp_path):
    configs = [_config(tmp_path, "a", STAC), _config(tmp_path, "b", STAC)]  # the same server in two configs: listed once
    out = list_tools(configs, tmp_path)
    assert [s["name"] for s in out["servers"]] == ["stac"] and out["servers"][0]["error"] is None
    names = {t["name"]: t for t in out["tools"]}
    assert set(names) == {"list_collections", "search_items", "get_item"}
    assert names["search_items"]["server"] == "stac" and "STAC" in names["search_items"]["description"]


def test_list_tools_records_server_error(tmp_path):
    bad = STAC + '''
[mcp.servers.broken]
command = "definitely-not-a-command-xyz"
package = "broken"
version = "0"
'''
    (tmp_path / "logs").mkdir()
    (tmp_path / "nope.toml").write_text("not = [valid")  # a config that doesn't load is skipped, not fatal
    out = list_tools([_config(tmp_path, "c", bad), tmp_path / "nope.toml"], tmp_path / "logs")
    by = {s["name"]: s for s in out["servers"]}
    assert "not found on PATH" in by["broken"]["error"] and by["stac"]["error"] is None  # the others still list
    assert {t["server"] for t in out["tools"]} == {"stac"} and out["skipped_configs"] == ["nope.toml"]


# --- T2: the API merges the cached menu with the tools runs called -------------------------------------------------

import json  # noqa: E402

import pytest  # noqa: E402
from conftest import FIXTURES  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from foundry.server import create_app  # noqa: E402


@pytest.fixture
def ui(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    shutil.copytree(ROOT / "tasks", root / "tasks", ignore=shutil.ignore_patterns("drafts", "sources"))
    shutil.copytree(ROOT / "config", root / "config")
    monkeypatch.setenv("FOUNDRY_HOME", str(root))
    runs = tmp_path / "runs"
    runs.mkdir()
    shutil.copytree(FIXTURES / "clean", runs / "fixture-clean")
    app = create_app(runs, root, port=8765)
    spawned = []
    from foundry.services import jobs

    def fake_spawn(st, kind, run_id, label, args, **kw):  # record the job instead of starting `uvx` servers
        class J:  # the parts of services.jobs.Job that tool_menu reads
            alive, exit_code = True, None
        j = J()
        j.kind, j.run_id, j.args = kind, run_id, args
        st.jobs[f"j{len(spawned)}"] = j
        spawned.append(j)
        return j

    monkeypatch.setattr(jobs, "spawn", fake_spawn)
    c = TestClient(app, base_url="http://127.0.0.1:8765", follow_redirects=False)
    return app, c, {"x-csrf-token": c.get("/api/session").json()["csrf"]}, spawned


def test_tool_menu_merges_cache_and_traces(ui):
    app, c, _, spawned = ui
    (app.state.ui.runs_dir / ".ui-tools.json").write_text(json.dumps({
        "listed_at": "2026-10-31T17:00:00Z",
        "servers": [{"name": "roda", "error": None}, {"name": "stac", "error": "McpStartError: no network"}],
        "tools": [{"server": "roda", "name": "search_datasets", "description": "Search RODA datasets by keyword."},
                  {"server": "roda", "name": "never_used", "description": "Listed but no run called it."}]}))
    body = c.get("/api/prompts/tools").json()
    by = {(t["server"], t["name"]): t for t in body["tools"]}
    assert by[("roda", "search_datasets")]["description"] == "Search RODA datasets by keyword."
    assert by[("roda", "search_datasets")]["runs"] == 1  # the fixture run called it
    assert by[("roda", "never_used")]["runs"] == 0  # the full menu, not only what runs used
    traced = [t for t in body["tools"] if t["runs"] and t["description"] is None]
    assert traced  # a tool runs called that the listing lacks still shows, without a description
    assert body["listed_at"] == "2026-10-31T17:00:00Z" and body["refreshing"] is False and not spawned
    assert body["errors"] == [{"server": "stac", "error": "McpStartError: no network"}]


def test_tool_menu_starts_listing_when_uncached(ui):
    app, c, h, spawned = ui
    body = c.get("/api/prompts/tools").json()
    assert body["refreshing"] is True and body["listed_at"] is None and body["tools"]  # trace tools meanwhile
    assert len(spawned) == 1 and spawned[0].kind == "tools" and spawned[0].args[:2] == ["tools", "--out"]
    c.get("/api/prompts/tools")
    assert len(spawned) == 1  # no second listing while one runs


def test_tools_refresh_409_when_running(ui):
    app, c, h, spawned = ui
    assert c.post("/api/prompts/tools/refresh").status_code == 403  # a write: needs the CSRF header
    assert c.post("/api/prompts/tools/refresh", headers=h).status_code == 200
    r = c.post("/api/prompts/tools/refresh", headers=h)
    assert r.status_code == 409 and "already being refreshed" in r.json()["error"]
    spawned[0].alive, spawned[0].exit_code = False, 1  # the listing failed without a cache: say so, don't relist per GET
    body = c.get("/api/prompts/tools").json()
    assert body["refreshing"] is False and body["errors"][0]["server"] == "*" and len(spawned) == 1


def test_tool_menu_matches_serverless_and_prefixed_names(ui):  # gate finding: no duplicates from imports or server/name
    app, c, h, _ = ui
    (app.state.ui.runs_dir / ".ui-tools.json").write_text(json.dumps({"listed_at": "x", "servers": [], "tools": [
        {"server": "roda", "name": "search_datasets", "description": "d"}, {"server": "stac", "name": "get_item", "description": "g"}]}))
    run = app.state.ui.runs_dir / "fixture-clean" / "trace.jsonl"
    text = run.read_text()
    assert '"server":"roda"' in text
    run.write_text(text.replace('"server":"roda"', '"server":null'))  # as an imported trace may record it
    assert c.put("/api/prompts/adb-006/stages", headers=h, json={"stages": [["stac/get_item"], ["unknown_tool"]]}).status_code == 200
    tools = c.get("/api/prompts/tools").json()["tools"]
    names = [(t["server"], t["name"]) for t in tools]
    assert names.count(("roda", "search_datasets")) == 1 and (None, "search_datasets") not in names
    assert next(t for t in tools if t["name"] == "search_datasets")["runs"] == 1
    assert names.count(("stac", "get_item")) == 1 and (None, "unknown_tool") in names


def test_failed_listing_keeps_the_previous_list(tmp_path, monkeypatch):  # gate pass 3: never overwrite a good list
    from foundry.cli import main

    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    _config(root / "config", "only-broken", '''[mcp.servers.broken]
command = "definitely-not-a-command-xyz"
package = "broken"
version = "0"
''')
    monkeypatch.setenv("FOUNDRY_HOME", str(root))
    out = tmp_path / ".ui-tools.json"
    good = '{"listed_at": "before", "servers": [], "tools": [{"server": "roda", "name": "x", "description": "kept"}]}'
    out.write_text(good)
    assert main(["tools", "--out", str(out)]) == 1
    assert out.read_text() == good and not list(tmp_path.glob(".ui-tools.json.tmp-*"))


def test_tool_menu_serverless_first_then_prefixed_is_one_entry(ui):  # verify finding: order must not make duplicates
    app, c, h, _ = ui
    (app.state.ui.runs_dir / ".ui-tools.json").write_text(json.dumps({"listed_at": "x", "servers": [], "tools": []}))
    run = app.state.ui.runs_dir / "fixture-clean" / "trace.jsonl"
    run.write_text(run.read_text().replace('"server":"roda"', '"server":null'))  # traces first, without a server
    assert c.put("/api/prompts/adb-006/stages", headers=h, json={"stages": [["roda/search_datasets"]]}).status_code == 200
    names = [(t["server"], t["name"]) for t in c.get("/api/prompts/tools").json()["tools"] if t["name"] == "search_datasets"]
    assert names == [("roda", "search_datasets")]
