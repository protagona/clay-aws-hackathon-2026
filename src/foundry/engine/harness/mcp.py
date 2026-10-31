"""Start the configured MCP servers over stdio and index their tools."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
import threading
import warnings
from dataclasses import dataclass, field
from typing import Any

from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from strands.tools.mcp import MCPClient

from foundry.config import McpServerCfg

# strands-agents 1.62.0 bug: when an MCP server fails to start, MCPClient.stop() schedules `_set_close_event` on an
# event loop that has already stopped, so Python warns that the coroutine was never awaited (mcp_client.py:684-690).
# The failure itself is handled and recorded (AC9c); only this exact message is silenced.
warnings.filterwarnings("ignore", message=r"coroutine 'MCPClient\.stop\.<locals>\._set_close_event' was never awaited",
                        category=RuntimeWarning)

ENV_ALLOWLIST = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT")


class McpStartError(Exception):
    def __init__(self, server: str, reason: str):
        self.server = server
        super().__init__(f"MCP server '{server}' failed to start: {reason}")


def server_env(cfg: McpServerCfg) -> dict[str, str]:
    """Never pass AWS credentials or other secrets to third-party servers: allowlist only."""
    env = {k: v for k, v in os.environ.items() if k in ENV_ALLOWLIST or k.startswith("UV_")}
    env.update(cfg.env)
    return env


@dataclass
class McpServers:
    clients: dict[str, MCPClient] = field(default_factory=dict)
    tools: list[Any] = field(default_factory=list)
    tool_server: dict[str, str] = field(default_factory=dict)
    tool_schema: dict[str, dict] = field(default_factory=dict)
    logs: list = field(default_factory=list)

    def stop(self, timeout: float = 2.0) -> None:
        """Stop every client without letting a hung subprocess block the caller for more than `timeout`."""

        def _stop_all():
            for client in self.clients.values():
                try:
                    client.stop(None, None, None)
                except Exception:
                    pass

        t = threading.Thread(target=_stop_all, daemon=True)
        t.start()
        t.join(timeout)
        for log in self.logs:
            log.close()


def start_servers(servers: dict[str, McpServerCfg], startup_timeout: int, log_dir: Path) -> McpServers:
    """Each server's stderr goes to <run_dir>/mcp-<name>.log (useful when a server fails to start)."""
    started = McpServers()
    try:
        for name, cfg in servers.items():
            # "{python}" means this interpreter, so in-repo servers (python -m foundry.engine.mcp_servers.*) need no PATH setup.
            command = sys.executable if cfg.command == "{python}" else shutil.which(cfg.command)
            if command is None:
                raise McpStartError(name, f"command '{cfg.command}' not found on PATH. Install it (see README) or fix [mcp.servers.{name}] command.")
            # Wrap the server in the reaper so it can't outlive a harness that is SIGKILLed.
            params = StdioServerParameters(command=sys.executable,
                                           args=["-m", "foundry.engine.harness.reaper", "--", command, *cfg.args],
                                           env=server_env(cfg))
            errlog = open(log_dir / f"mcp-{name}.log", "a", encoding="utf-8")  # noqa: SIM115 (lives as long as the server)
            started.logs.append(errlog)
            client = MCPClient(lambda p=params, e=errlog: stdio_client(p, errlog=e), startup_timeout=startup_timeout)
            try:
                client.start()
                tools = list(client.list_tools_sync())
            except Exception as exc:
                raise McpStartError(name, _root_cause(exc)) from exc
            started.clients[name] = client
            for tool in tools:
                started.tools.append(tool)
                started.tool_server[tool.tool_name] = name
                started.tool_schema[tool.tool_name] = tool.tool_spec.get("inputSchema", {}).get("json", {})
    except BaseException:
        started.stop()
        raise
    return started


def _root_cause(exc: BaseException) -> str:
    seen = exc
    while seen.__cause__ is not None:
        seen = seen.__cause__
    if isinstance(seen, BaseExceptionGroup) and seen.exceptions:
        seen = seen.exceptions[0]
    return f"{type(seen).__name__}: {seen}"[:500]
