"""Increment 15, T1: `foundry tools` lists every tool of the configs' MCP servers, with descriptions."""

import re
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


class Listings:
    """The tool-listing jobs a test started, on either setup: the laptop's spawns are recorded instead of starting
    `uvx` servers; the cloud's are queued for a worker that never runs here."""

    def __init__(self, h, monkeypatch):
        self.h, self.spawned = h, []
        if h.name == "laptop":
            from foundry.services import jobs

            def fake_spawn(st, kind, run_id, label, args, **kw):
                class J:  # the parts of services.jobs.Job that tool_menu reads
                    alive, exit_code, status = True, None, "running"
                j = J()
                j.kind, j.run_id, j.args = kind, run_id, args
                st.jobs[f"j{len(self.spawned)}"] = j
                self.spawned.append(j)
                return j

            monkeypatch.setattr(jobs, "spawn", fake_spawn)

    def started(self) -> list[list[str]]:
        """Each started listing's command."""
        if self.h.name == "laptop":
            return [j.args for j in self.spawned]
        items = [self.h.store.get_item("JOB", j) for j in self.h.queue]
        return [json.loads(i["args"]) for i in items if i and i["kind"] == "tools"]

    def fail(self) -> None:
        """The listing ended in failure without leaving a list."""
        if self.h.name == "laptop":
            self.spawned[0].alive, self.spawned[0].exit_code = False, 1
        else:
            import time

            from foundry.services import cloud_jobs

            cloud_jobs.finish(self.h.store, self.h.queue[0], "failed", 1, "no servers", time.time())


@pytest.fixture
def ui(backend, monkeypatch):
    """The app on both setups (tests/backends.py) with a run that called roda/search_datasets."""
    monkeypatch.setenv("FOUNDRY_HOME", str(backend.project))
    backend.seed("clean")
    return backend, backend.client, backend.headers(), Listings(backend, monkeypatch)


def test_tool_menu_merges_cache_and_traces(ui):
    h, c, _, listings = ui
    h.set_tool_cache(({
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
    assert body["listed_at"] == "2026-10-31T17:00:00Z" and body["refreshing"] is False and not listings.started()
    assert body["errors"] == [{"server": "stac", "error": "McpStartError: no network"}]


def test_tool_menu_starts_listing_when_uncached(ui):
    _, c, _, listings = ui
    body = c.get("/api/prompts/tools").json()
    assert body["refreshing"] is True and body["listed_at"] is None and body["tools"]  # trace tools meanwhile
    (args,) = listings.started()
    assert args[:2] == ["tools", "--out"]
    c.get("/api/prompts/tools")
    assert len(listings.started()) == 1  # no second listing while one runs


def test_tools_refresh_409_when_running(ui):
    _, c, h, listings = ui
    assert c.post("/api/prompts/tools/refresh").status_code == 403  # a write: needs the CSRF header
    assert c.post("/api/prompts/tools/refresh", headers=h).status_code == 200
    r = c.post("/api/prompts/tools/refresh", headers=h)
    assert r.status_code == 409 and "already being refreshed" in r.json()["error"]
    listings.fail()  # the listing failed without a cache: say so, don't relist per GET
    body = c.get("/api/prompts/tools").json()
    assert body["refreshing"] is False and body["errors"][0]["server"] == "*" and len(listings.started()) == 1


def test_tool_menu_matches_serverless_and_prefixed_names(ui):  # gate finding: no duplicates from imports or server/name
    hn, c, h, _ = ui
    hn.set_tool_cache({"listed_at": "x", "servers": [], "tools": [
        {"server": "roda", "name": "search_datasets", "description": "d"}, {"server": "stac", "name": "get_item", "description": "g"}]})
    run = hn.runs_dir / "fixture-clean" / "trace.jsonl"
    text = run.read_text()
    assert '"server":"roda"' in text
    run.write_text(text.replace('"server":"roda"', '"server":null'))  # as an imported trace may record it
    hn.sync()
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
    hn, c, h, _ = ui
    hn.set_tool_cache({"listed_at": "x", "servers": [], "tools": []})
    run = hn.runs_dir / "fixture-clean" / "trace.jsonl"
    run.write_text(run.read_text().replace('"server":"roda"', '"server":null'))  # traces first, without a server
    hn.sync()
    assert c.put("/api/prompts/adb-006/stages", headers=h, json={"stages": [["roda/search_datasets"]]}).status_code == 200
    names = [(t["server"], t["name"]) for t in c.get("/api/prompts/tools").json()["tools"] if t["name"] == "search_datasets"]
    assert names == [("roda", "search_datasets")]
