"""The tool menu on the Prompts page (increment 15): every tool the MCP servers offer (a cached `foundry tools`
listing, refreshed in the background), plus any tool seen in traces or named in a prompt's expected steps."""

from __future__ import annotations

from foundry.data import tool_cache
from foundry.services import cloud_state, jobs, prompts, runs
from foundry.services.workspace import Workspace


def refresh_tools(ws: Workspace, *, quiet: bool = False) -> None:
    """Start `foundry tools` in the background (409 if one is already running, unless quiet)."""
    jobs.start(ws, "tools", "tools", "Tool listing", ["tools", "--out", str(tool_cache.path(ws.runs_dir))],
               singleton=True, quiet=quiet)


def tool_menu(ws: Workspace, task: str) -> dict:
    """Every tool: the servers' full menu (cached listing) plus anything seen in traces or expected steps (T2)."""
    cloud_state.pull_tools(ws)
    store = prompts.prompt_store(ws, task)
    used: dict[tuple, int] = {}  # (server, name) -> runs that called it
    for e in runs.entries(ws):
        seen = set()
        for s in e["steps"]:
            if getattr(s, "type", None) == "tool_call" and isinstance(getattr(s, "name", None), str):
                server = s.server if isinstance(getattr(s, "server", None), str) and s.server else None
                seen.add((server, s.name))
        for key in seen:
            used[key] = used.get(key, 0) + 1
    cache = tool_cache.read(ws.runs_dir)
    tools: dict[tuple, dict] = {}
    for t in (cache or {}).get("tools", []):
        if isinstance(t, dict) and isinstance(t.get("name"), str) and isinstance(t.get("server"), str):
            desc = t.get("description")
            server, name = t["server"][:64], t["name"][:128]
            tools[(server, name)] = {"server": server, "name": name,
                                     "description": desc[:600] if isinstance(desc, str) else None, "runs": 0}

    def entry(server, name) -> dict:
        """The menu entry for a tool: an exact (server, name) match, else the only entry with that bare name (a trace
        step or expected step without a server), else a new entry with no description (grouped under "other")."""
        if (server, name) in tools:
            return tools[(server, name)]
        if server is not None and (None, name) in tools:  # a serverless entry seen first: it is this server's tool
            t = tools.pop((None, name))
            t["server"] = server
            tools[(server, name)] = t
            return t
        if server is None:
            same = [t for (s, n), t in tools.items() if n == name]
            if len(same) == 1:
                return same[0]
        return tools.setdefault((server, name), {"server": server, "name": name, "description": None, "runs": 0})

    for (server, name), n in used.items():
        entry(server, name)["runs"] += n
    for r in prompts.call(ws, store.rows):
        for step in r["stages"] or []:
            for t in step:
                server, _, name = t.rpartition("/")
                entry(server or None, name)
    listings = jobs.of_kind(ws, "tools")
    refreshing = any(j.alive for j in listings)
    if cache is None and not listings:  # list once per UI session; a failed listing is reported, not retried per GET
        refresh_tools(ws, quiet=True)
        refreshing = True
    errors = [{"server": str(s.get("name"))[:64], "error": s["error"][:500]} for s in (cache or {}).get("servers", [])
              if isinstance(s, dict) and isinstance(s.get("error"), str)]
    last = listings[-1] if listings else None
    if not refreshing and last is not None and last.exit_code not in (0, None):
        errors.append({"server": "*", "error": "The last tool listing failed; see the job log. Refresh to try again."
                       + (" Showing the list from before." if cache is not None else "")})
    return {"tools": sorted(tools.values(), key=lambda t: (t["server"] or "~", t["name"])),
            "listed_at": cache["listed_at"][:40] if cache and isinstance(cache.get("listed_at"), str) else None,
            "errors": errors, "refreshing": refreshing}
