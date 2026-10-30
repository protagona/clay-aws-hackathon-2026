"""Test-only MCP server for fault injection (real RODA can't fail on demand).

Run as: python tests/mcp_fault_server.py [--crash-on-start]
"""

import sys
import time

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

if "--crash-on-start" in sys.argv:
    print("fault server: crashing on start as requested", file=sys.stderr)
    sys.exit(3)

server = MCPServer("fault")


@server.tool()
def echo(text: str) -> str:
    """Return the text unchanged."""
    return text


@server.tool()
def fail(message: str) -> str:
    """Always fail with the given message."""
    raise ToolError(message)


@server.tool()
def sleep(seconds: float) -> str:
    """Sleep, then return."""
    time.sleep(seconds)
    return f"slept {seconds}"


@server.tool()
def big(chars: int) -> str:
    """Return a string of the given length."""
    return "x" * chars


@server.tool()
def json_error() -> str:
    """Report an error the RODA way: inside a successful result."""
    return '{"status": "error", "error": "Dataset not found: nope"}'


if __name__ == "__main__":
    server.run("stdio")
