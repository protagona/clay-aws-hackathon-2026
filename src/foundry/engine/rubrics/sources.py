"""Where a rubric comes from (increment 8, I1-I4): an uploaded file, pasted text, an https URL, or an MCP tool.

Every source is untrusted bytes with a known format. URL fetches refuse non-public addresses on every hop (SSRF);
the MCP source can only start servers already named in a config.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from foundry.data.rubrics import Source  # the record lives in data (ADR-3); fetching sources stays here

MAX_BYTES = 4_500_000  # our cap per source (Bedrock's per-document limit is not stated in its API reference)
MAX_TEXT = 50_000
URL_TIMEOUT_S = 10
MAX_REDIRECTS = 3
MCP_TIMEOUT_S = 60
FORMATS = {".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx", ".csv": "csv", ".md": "md", ".markdown": "md",
           ".txt": "txt", ".html": "html", ".htm": "html", ".json": "json"}
BINARY = {"pdf", "docx", "xlsx", "csv"}  # sent to Bedrock as document blocks; the rest is sent as text
_CTYPES = {"application/pdf": "pdf", "text/csv": "csv", "text/markdown": "md", "text/plain": "txt",
           "text/html": "html", "application/json": "json",
           "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx"}


class SourceError(Exception):
    pass




def allowed_types() -> str:
    return " ".join(sorted(set(FORMATS)))


def from_file(name: str, data: bytes) -> Source:
    ext = Path(name).suffix.lower()
    if ext not in FORMATS:
        raise SourceError(f"Unsupported file type '{ext or name}'. Allowed: {allowed_types()}")
    if not data:
        raise SourceError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise SourceError(f"The file is over the {MAX_BYTES / 1e6:.1f} MB limit.")
    return Source("file", Path(name).name[:200], FORMATS[ext], data)


def from_text(text: str) -> Source:
    text = (text or "").strip()
    if not text:
        raise SourceError("Paste some rubric text first.")
    if len(text) > MAX_TEXT:
        raise SourceError(f"The text is over {MAX_TEXT:,} characters.")
    return Source("text", "pasted text", "txt", text.encode())


# --- URL ------------------------------------------------------------------------------------------------------


def _public_host(host: str, port: int) -> None:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SourceError(f"Cannot resolve {host}: {exc.strerror}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:  # ::ffff:127.0.0.1 is 127.0.0.1
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:  # private, loopback, link-local, CGNAT, reserved, unspecified...
            raise SourceError(f"Refusing to fetch {host}: it resolves to a non-public address ({ip}).")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # we follow redirects ourselves, checked
        return None


def from_url(url: str, *, allow_http: bool = False) -> Source:
    """allow_http (keyword-only) exists for tests against a local server and also skips the address check; no UI
    route or CLI flag passes it."""
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))  # no env proxies: the
    # address check must apply to the host we actually connect to
    current = (url or "").strip()
    for _ in range(MAX_REDIRECTS + 1):
        parts = urllib.parse.urlsplit(current)
        if parts.scheme != "https" and not (allow_http and parts.scheme == "http"):
            raise SourceError("Only https:// URLs can be fetched.")
        if not parts.hostname:
            raise SourceError("That URL has no host.")
        if not allow_http:
            _public_host(parts.hostname, parts.port or 443)
        try:
            resp = opener.open(urllib.request.Request(current, headers={"User-Agent": "foundry-rubric-import"}),
                               timeout=URL_TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308) and exc.headers.get("Location"):
                current = urllib.parse.urljoin(current, exc.headers["Location"])
                continue
            raise SourceError(f"The URL returned HTTP {exc.code}.") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SourceError(f"Could not fetch the URL: {getattr(exc, 'reason', exc)}") from None
        with resp:
            body = resp.read(MAX_BYTES + 1)
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if len(body) > MAX_BYTES:
            raise SourceError(f"The URL's content is over the {MAX_BYTES / 1e6:.1f} MB limit.")
        if not body:
            raise SourceError("The URL returned no content.")
        fmt = _CTYPES.get(ctype) or FORMATS.get(Path(parts.path).suffix.lower()) or "txt"
        return Source("url", current[:500], fmt, body)
    raise SourceError(f"Too many redirects (more than {MAX_REDIRECTS}).")


# --- MCP tool ---------------------------------------------------------------------------------------------------


def from_mcp(cfg, server: str, tool: str, args: dict | None = None) -> Source:
    """Call one tool on a server from the config's [mcp.servers] (S3: no free-form commands) and take its text."""
    from foundry.engine.harness.mcp import McpStartError, start_servers

    if server not in cfg.mcp.servers:
        raise SourceError(f"Unknown MCP server '{server}'. This config has: {', '.join(cfg.mcp.servers) or 'none'}")
    if not isinstance(args or {}, dict):
        raise SourceError("Tool arguments must be a JSON object.")
    from datetime import timedelta

    with tempfile.TemporaryDirectory(prefix="foundry-rubric-mcp-") as logs:
        try:
            servers = start_servers({server: cfg.mcp.servers[server]}, cfg.limits.mcp_start_timeout_s, Path(logs))
        except McpStartError as exc:
            raise SourceError(str(exc)) from None
        try:
            if tool not in servers.tool_server:
                raise SourceError(f"Server '{server}' has no tool '{tool}'. It has: {', '.join(sorted(servers.tool_server))}")
            result = servers.clients[server].call_tool_sync("rubric-import", tool, args or {},
                                                            read_timeout_seconds=timedelta(seconds=MCP_TIMEOUT_S))
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(f"The MCP tool call failed: {type(exc).__name__}: {str(exc)[:300]}") from None
        finally:
            servers.stop()
    text = "".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))
    if result.get("status") == "error":
        raise SourceError(f"The MCP tool returned an error: {text[:300]}")
    if not text.strip():
        raise SourceError("The MCP tool returned no text.")
    data = text.encode()
    if len(data) > MAX_BYTES:
        raise SourceError(f"The MCP tool's result is over the {MAX_BYTES / 1e6:.1f} MB limit.")
    fmt = "json" if _is_json(text) else "txt"
    return Source("mcp", f"{server}/{tool} {json.dumps(args or {})[:200]}", fmt, data)


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except ValueError:
        return False
