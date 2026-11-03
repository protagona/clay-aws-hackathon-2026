"""NAIP tile MCP server (requirement-gaps design, slice C): one tool, naip_tile(tile_id), returning a 256x256 RGB
PNG of one tile of the staged caption sample plus its footprint and date.

Reads only $NAIP_SAMPLE_DIR/{sample.tif,tiles.json}, a local directory the harness resolves from "{sample:naip-captions}"
(it downloads the sample from the deployment's runs bucket first). The server gets no AWS credentials and never reads
S3 or NAIP's requester-pays buckets.
"""

from __future__ import annotations

import json
import os
import re
import warnings
from functools import lru_cache
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

TILE = 256
GSD_M = 0.6
_TILE_ID = re.compile(r"^t-r0[0-7]-c0[0-7]$")

server = MCPServer("naip")


@lru_cache(maxsize=1)
def _sample() -> tuple[Path, dict, dict]:
    """(sample.tif path, metadata, tiles by id). Checks that tiles.json describes sample.tif (same staging)."""
    d = os.environ.get("NAIP_SAMPLE_DIR", "")
    tif, meta_path = Path(d) / "sample.tif", Path(d) / "tiles.json"
    if not d or not tif.is_file() or not meta_path.is_file():
        raise ToolError("NAIP sample not available (NAIP_SAMPLE_DIR is unset, empty, or has no sample.tif); "
                        "the run note says how to stage it.")
    meta = json.loads(meta_path.read_text())
    tiles = {t["tile_id"]: t for t in meta["tiles"]}
    _check_matches(tif, tiles)
    return tif, meta, tiles


def _check_matches(tif: Path, tiles: dict) -> None:
    import rasterio
    from rasterio.warp import transform_bounds

    with rasterio.open(tif) as ds:
        w, s, e, n = transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)
    boxes = [t["bbox"] for t in tiles.values()]
    union = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    if max(abs(a - b) for a, b in zip((w, s, e, n), union)) > 1e-4:
        raise ToolError("NAIP sample is inconsistent: tiles.json doesn't describe sample.tif (re-stage the sample)")


def _png(tif: Path, row: int, col: int) -> bytes:
    import rasterio
    import rasterio.errors
    from rasterio.io import MemoryFile
    from rasterio.windows import Window

    with rasterio.open(tif) as ds:
        rgb = ds.read([1, 2, 3], window=Window(col * TILE, row * TILE, TILE, TILE))
    with warnings.catch_warnings():  # a PNG has no georeferencing; that is expected here
        warnings.simplefilter("ignore", rasterio.errors.NotGeoreferencedWarning)
        with MemoryFile() as mem:
            with mem.open(driver="PNG", width=TILE, height=TILE, count=3, dtype="uint8") as png:
                png.write(rgb)
            return mem.read()


@server.tool(structured_output=False)
def naip_tile(tile_id: str) -> list:
    """Get one NAIP aerial image tile (256x256 px, 0.6 m per pixel, RGB) by id, with its footprint (lon/lat bbox),
    acquisition date and source image. Tile ids look like t-r03-c05; the prompt names the tile to caption."""
    tile_id = (tile_id or "").strip()
    if not _TILE_ID.match(tile_id):
        raise ToolError(f"Unknown tile {tile_id[:40]!r}. Tile ids look like t-r03-c05 (row and column 00–07); "
                        "the prompt names the tile to caption.")
    tif, meta, tiles = _sample()
    tile = tiles.get(tile_id)
    if tile is None:
        raise ToolError(f"Unknown tile {tile_id[:40]!r}. Tile ids look like t-r03-c05 (row and column 00–07); "
                        "the prompt names the tile to caption.")
    src = meta.get("source", {})
    info = {"tile_id": tile_id, "bbox": tile["bbox"], "acquired": src.get("acquired"),
            "source": src.get("cog"), "gsd_m": GSD_M, "size_px": TILE}
    return [Image(data=_png(tif, tile["row"], tile["col"]), format="png"), json.dumps(info)]


if __name__ == "__main__":
    server.run("stdio")
