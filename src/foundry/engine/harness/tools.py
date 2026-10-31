"""Every tool the agent can call, with its description (increment 15): start each distinct MCP server the configs use,
once, list its tools, stop it. Used by `foundry tools`, which the UI runs as a background job and caches.
"""

from __future__ import annotations

import time
from pathlib import Path

from foundry.config import ConfigError, McpServerCfg, load_config
from foundry.engine.harness.mcp import McpStartError, start_servers

DESCRIPTION_MAX = 600


def _servers(configs: list[Path]) -> tuple[dict[tuple, tuple[str, McpServerCfg, int]], list[str]]:
    """Distinct servers by (name, package, version) across every config that loads, with that config's start timeout."""
    found: dict[tuple, tuple[str, McpServerCfg, int]] = {}
    skipped = []
    for path in configs:
        try:
            cfg = load_config(path)
        except ConfigError:
            skipped.append(path.name)
            continue
        for name, server in cfg.mcp.servers.items():
            found.setdefault((name, server.package, server.version), (name, server, cfg.limits.mcp_start_timeout_s))
    return found, skipped


def list_tools(configs: list[Path], log_dir: Path) -> dict:
    servers, skipped = _servers(configs)
    out_servers, tools = [], []
    for (name, package, version), (_, cfg, timeout) in sorted(servers.items(), key=lambda kv: kv[0][0]):
        entry = {"name": name, "package": package, "version": version, "error": None}
        started = None
        try:
            started = start_servers({name: cfg}, timeout, log_dir)
            for tool in started.tools:
                spec = tool.tool_spec if isinstance(tool.tool_spec, dict) else {}
                desc = spec.get("description")
                tools.append({"server": name, "name": tool.tool_name,
                              "description": desc.strip()[:DESCRIPTION_MAX] if isinstance(desc, str) else None})
        except McpStartError as exc:
            entry["error"] = str(exc)[:500]
        except Exception as exc:  # a server that misbehaves while listing must not stop the others
            entry["error"] = f"{type(exc).__name__}: {exc}"[:500]
        finally:
            if started is not None:
                started.stop()
        out_servers.append(entry)
    return {"listed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "servers": out_servers,
            "tools": sorted(tools, key=lambda t: (t["server"], t["name"])), "skipped_configs": skipped}
