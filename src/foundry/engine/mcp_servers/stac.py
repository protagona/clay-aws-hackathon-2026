"""A small STAC MCP server: find actual imagery scenes (items) for an area and a date range.

Complements RODA, which discovers datasets. Talks to any STAC API with item search; the default is Earth Search
(public, no credentials). Responses are compact on purpose: one raw Sentinel-2 item is ~20 KB of JSON, which would
swamp an agent's context, so items are reduced to what a dataset-build plan needs (id, date, cloud cover,
footprint, and the hrefs of a few key assets).

Run: python -m foundry.engine.mcp_servers.stac        (stdio; STAC_API_URL overrides the endpoint)
"""

from __future__ import annotations

import json
import logging
import os
import re
from urllib.parse import quote

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

DEFAULT_API = "https://earth-search.aws.element84.com/v1"
TIMEOUT_S = float(os.environ.get("STAC_TIMEOUT_S", "20"))
MAX_LIMIT = 20
KEY_ASSETS = ("visual", "image", "red", "green", "blue", "nir", "nir08", "swir16", "scl", "qa_pixel", "data",
              "thumbnail")
MAX_HREFS = 5
MAX_ASSETS_LISTED = 40
_DATETIME = re.compile(r"^(\.\.|\d{4}-\d{2}-\d{2}(T[0-9:.]+Z?)?)(/(\.\.|\d{4}-\d{2}-\d{2}(T[0-9:.]+Z?)?))?$")

server = MCPServer("stac")
logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request would flood mcp-stac.log
_collections_cache: list[dict] | None = None


def _api() -> str:
    return os.environ.get("STAC_API_URL", DEFAULT_API).rstrip("/")


def _request(method: str, path: str, **kw) -> dict:
    url = f"{_api()}{path}"
    try:
        r = httpx.request(method, url, timeout=TIMEOUT_S, **kw)
    except httpx.TimeoutException:
        raise ToolError(f"STAC API timed out after {TIMEOUT_S:g}s ({url}). Try a smaller bbox or date range.") from None
    except httpx.HTTPError as exc:
        raise ToolError(f"STAC API unreachable ({url}): {type(exc).__name__}") from None
    if r.status_code >= 400:
        detail = r.text[:300].replace("\n", " ")
        raise ToolError(f"STAC API returned HTTP {r.status_code} for {path}: {detail}")
    try:
        return r.json()
    except ValueError:
        raise ToolError(f"STAC API returned non-JSON for {path}") from None


def _collections() -> list[dict]:
    global _collections_cache
    if _collections_cache is None:
        _collections_cache = _request("GET", "/collections").get("collections", [])
    return _collections_cache


def _check_collection(collection: str) -> None:
    ids = [c["id"] for c in _collections()]
    if collection not in ids:
        raise ToolError(f"Unknown collection '{collection}'. Available: {', '.join(ids)}")


def _check_bbox(bbox: list[float]) -> list[float]:
    if len(bbox) != 4:
        raise ToolError("bbox must be [west, south, east, north] in degrees (4 numbers)")
    w, s, e, n = bbox
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ToolError(f"bbox {bbox} is not [west, south, east, north] with west<east and south<north in degrees")
    return [w, s, e, n]


def _interval(datetime: str) -> str:
    """Dates become whole-day RFC 3339 bounds: "2022-06-01/2022-06-30" -> 00:00:00Z on the 1st to 23:59:59Z on the 30th."""
    parts = datetime.strip().split("/")
    if len(parts) == 1:
        parts = [parts[0], parts[0]]
    start, end = parts
    if start != ".." and "T" not in start:
        start += "T00:00:00Z"
    if end != ".." and "T" not in end:
        end += "T23:59:59Z"
    return f"{start}/{end}"


def _compact(item: dict) -> dict:
    assets = item.get("assets", {})
    keys = [k for k in KEY_ASSETS if k in assets][:MAX_HREFS]
    props = item.get("properties", {})
    return {
        "id": item.get("id"),
        "collection": item.get("collection"),
        "datetime": props.get("datetime"),
        "cloud_cover": props.get("eo:cloud_cover"),
        "bbox": [round(v, 4) for v in item.get("bbox", [])],
        "assets": [k for k in assets if not k.endswith("-jp2")][:MAX_ASSETS_LISTED],
        "hrefs": {k: assets[k].get("href") for k in keys},
    }


@server.tool(structured_output=False)
def list_collections() -> str:
    """List the imagery collections the STAC API offers (id, title, temporal extent, license)."""
    out = []
    for c in _collections():
        interval = (c.get("extent", {}).get("temporal", {}).get("interval") or [[None, None]])[0]
        out.append({"id": c["id"], "title": c.get("title"), "from": interval[0], "to": interval[1],
                    "license": c.get("license")})
    return json.dumps({"api": _api(), "collections": out})


@server.tool(structured_output=False)
def search_items(collection: str, bbox: list[float], datetime: str, limit: int = 5,
                 max_cloud_cover: float | None = None) -> str:
    """Find scenes (STAC items) in one collection that intersect bbox [west, south, east, north] (degrees) within
    datetime ("2022-06-01/2022-06-30", or a single date). Optionally keep only scenes with cloud cover below
    max_cloud_cover percent. Returns how many matched and up to `limit` (max 20) compact items with asset hrefs."""
    _check_collection(collection)
    box = _check_bbox(bbox)
    if not _DATETIME.match(datetime.strip()):
        raise ToolError('datetime must be "YYYY-MM-DD", "YYYY-MM-DD/YYYY-MM-DD" or RFC 3339; open ends use ".."')
    if not 1 <= limit <= MAX_LIMIT:
        raise ToolError(f"limit must be between 1 and {MAX_LIMIT}")
    body: dict = {"collections": [collection], "bbox": box, "datetime": _interval(datetime), "limit": limit}
    if max_cloud_cover is not None:
        body["query"] = {"eo:cloud_cover": {"lt": max_cloud_cover}}
    data = _request("POST", "/search", json=body)
    items = [_compact(f) for f in data.get("features", [])][:limit]
    matched = data.get("numberMatched", data.get("context", {}).get("matched"))
    return json.dumps({"collection": collection, "matched": matched, "returned": len(items), "items": items})


@server.tool(structured_output=False)
def get_item(collection: str, item_id: str) -> str:
    """Get one scene's details: date, platform, cloud cover, projection, and every asset with its href and type."""
    _check_collection(collection)
    item = _request("GET", f"/collections/{collection}/items/{quote(item_id, safe='')}")
    props = item.get("properties", {})
    keep = ("datetime", "platform", "eo:cloud_cover", "gsd", "proj:epsg", "proj:code", "s2:mgrs_tile",
            "landsat:wrs_path", "landsat:wrs_row")
    assets = [{"key": k, "href": a.get("href"), "type": a.get("type"), "title": a.get("title")}
              for k, a in item.get("assets", {}).items() if not k.endswith("-jp2")][:MAX_ASSETS_LISTED]
    return json.dumps({"id": item.get("id"), "collection": collection, "bbox": item.get("bbox"),
                       "properties": {k: props[k] for k in keep if k in props}, "assets": assets})


if __name__ == "__main__":
    server.run("stdio")
