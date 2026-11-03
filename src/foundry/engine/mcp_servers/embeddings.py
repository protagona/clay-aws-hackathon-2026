"""An MCP server over Clay v1.5 embeddings (LGND, Sentinel-2 L2A, 1280 m cells, 256 dims): coverage, similar places,
change. (clay-embeddings design.)

The data is public GeoParquet on Source Cooperative (anonymous, us-west-2, CC BY 4.0), hive-partitioned by geohash
(2 characters), year and month. One partition-month is a single file of up to ~345 MB, almost all of it embeddings,
in two row groups, so a query reads about the whole partition whatever the box: boxes are capped at 1 degree and
2 partitions per month (design ADR-4). Coverage needs only an S3 listing; the other two tools query with DuckDB
(httpfs) and are serialised, because the agent may call tools concurrently.

Run: python -m foundry.engine.mcp_servers.embeddings        (stdio; EMBEDDINGS_ROOT overrides the data location)
"""

from __future__ import annotations

import json
import os
import re
import statistics
import threading
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

DEFAULT_ROOT = ("s3://us-west-2.opendata.source.coop/clay/lgnd-embeddings/monthly-aggregated/"
                "model_version=v1.5/collection=sentinel-2-l2a/chip_size=1280m/dims=256")
BUNDLED_EXTENSIONS = "/opt/duckdb-extensions"
DIMS = 256
MAX_BOX_DEG = 1.0
MAX_COVERAGE_DEG = 10.0
MAX_PARTITIONS = 2
MAX_K = 25
MOST_CHANGED = 10
MAX_SCENES = 5
COVERAGE_BUDGET_S = 30.0
DATASET = "Clay v1.5 embeddings by LGND, Sentinel-2 L2A, 1280 m cells, 256 dims, CC BY 4.0"
_B32 = "0123456789bcdefghjkmnpqrstuvwxyz"
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_KEY_MONTH = re.compile(r"year=(\d{4})/month=(\d{1,2})/")

server = MCPServer("embeddings")
_lock = threading.Lock()
_con = None
_s3 = None


def _root() -> str:
    return os.environ.get("EMBEDDINGS_ROOT", DEFAULT_ROOT).rstrip("/")


def _timeout_s() -> float:
    return float(os.environ.get("EMBEDDINGS_TIMEOUT_S", "60"))


# --- geometry ------------------------------------------------------------------------------------------------------

def geohash2(lat: float, lon: float) -> str:
    """Standard base-32 geohash at precision 2 (11.25 deg wide, 5.625 deg tall)."""
    lat_r, lon_r = [-90.0, 90.0], [-180.0, 180.0]
    out, ch, bits, even = "", 0, 0, True
    while len(out) < 2:
        rng, val = (lon_r, lon) if even else (lat_r, lat)
        mid = (rng[0] + rng[1]) / 2
        if val >= mid:
            ch, rng[0] = (ch << 1) | 1, mid
        else:
            ch, rng[1] = ch << 1, mid
        even, bits = not even, bits + 1
        if bits == 5:
            out, ch, bits = out + _B32[ch], 0, 0
    return out


def _steps(lo: float, hi: float, step: float) -> list[float]:
    vals, v = [], lo
    while v < hi:
        vals.append(v)
        v += step
    return vals + [hi]


def partitions(bbox: list[float]) -> list[str]:
    """Every geohash-2 partition the box touches: a grid finer than one partition, plus the far edges."""
    w, s, e, n = bbox
    found = []
    for lat in _steps(s, n, 2.5):
        for lon in _steps(w, e, 5.0):
            g = geohash2(min(lat, 89.999999), min(lon, 179.999999))
            if g not in found:
                found.append(g)
    return found


def _check_bbox(bbox: list[float], limit: float, name: str = "bbox") -> list[float]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ToolError(f"{name} must be [west, south, east, north] in degrees (4 numbers)")
    w, s, e, n = (float(v) for v in bbox)
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ToolError(f"{name} {list(bbox)} is not [west, south, east, north] with west<east and south<north "
                        "in degrees")
    if e - w > limit or n - s > limit:
        what = "Coverage box" if limit == MAX_COVERAGE_DEG else name
        hint = "" if limit == MAX_COVERAGE_DEG else " Split it into smaller boxes."
        raise ToolError(f"{what} spans {e - w:g}° x {n - s:g}°; the limit is {limit:g}° x {limit:g}°.{hint}")
    return [w, s, e, n]


def _check_month(month: str, name: str = "month") -> tuple[int, int]:
    m = _MONTH.match(str(month).strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ToolError(f'{name} must be "YYYY-MM", e.g. "2024-07"')
    return int(m.group(1)), int(m.group(2))


def _r(v: float, nd: int) -> float:
    return round(float(v), nd)


def _box(b: dict) -> list[float]:
    return [_r(b["xmin"], 4), _r(b["ymin"], 4), _r(b["xmax"], 4), _r(b["ymax"], 4)]


# --- listing (no DuckDB) -------------------------------------------------------------------------------------------

def _split_s3(uri: str) -> tuple[str, str]:
    bucket, _, key = uri[len("s3://"):].partition("/")
    return bucket, key


def _client():
    global _s3
    if _s3 is None:
        import boto3
        from botocore import UNSIGNED
        from botocore.config import Config

        _s3 = boto3.client("s3", region_name="us-west-2", config=Config(
            signature_version=UNSIGNED, connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 2}))
    return _s3


def _keys(partition: str) -> list[str]:
    """Every file key (relative) under one partition: S3 listing, or the filesystem for a local root (tests)."""
    root = _root()
    if not root.startswith("s3://"):
        base = Path(root) / f"geohash={partition}"
        return [p.relative_to(base).as_posix() for p in base.rglob("*.parquet")] if base.is_dir() else []
    bucket, key = _split_s3(root)
    prefix = f"{key}/geohash={partition}/"
    out = []
    try:
        for page in _client().get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            out += [o["Key"][len(prefix):] for o in page.get("Contents", [])]
    except Exception as exc:  # noqa: BLE001 - any listing failure is reported to the agent
        raise ToolError(f"could not list embeddings in S3: {type(exc).__name__}: {str(exc)[:200]}") from None
    return out


def _months(partition: str) -> list[str]:
    found = set()
    for k in _keys(partition):
        m = _KEY_MONTH.search(k if k.startswith("year=") else "/" + k)
        if m:
            found.add(f"{int(m.group(1)):04d}-{int(m.group(2)):02d}")
    return sorted(found)


def _gaps(months: list[str]) -> list[str]:
    if not months:
        return []
    y, m = map(int, months[0].split("-"))
    have, out = set(months), []
    while f"{y:04d}-{m:02d}" <= months[-1]:
        if f"{y:04d}-{m:02d}" not in have:
            out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _nearest(months: list[str], month: str, n: int = 2) -> list[str]:
    def idx(s: str) -> int:
        y, m = map(int, s.split("-"))
        return y * 12 + m

    return sorted(months, key=lambda s: (abs(idx(s) - idx(month)), s))[:n]


def _files(parts: list[str], year: int, month: int) -> list[str]:
    """The data files for these partitions in one month, or a ToolError naming what's missing."""
    label = f"{year:04d}-{month:02d}"
    missing = [g for g in parts if label not in _months(g)]
    if missing:
        if len(missing) == len(parts):
            near = ", ".join(_nearest(_months(missing[0]), label)) or "none"
            raise ToolError(f"No embeddings for {label} in partition {missing[0]}. Nearest months with data: {near}. "
                            "Call embedding_coverage for the full list.")
        have = [g for g in parts if g not in missing]
        raise ToolError(f"{label} has embeddings in partition {', '.join(have)} but not {', '.join(missing)}; this "
                        "box needs both. Move the box or pick a month both have (call embedding_coverage).")
    root = _root()
    return [f"{root}/geohash={g}/year={year}/month={month:02d}/*.parquet" for g in parts]


# --- DuckDB --------------------------------------------------------------------------------------------------------

def _connection():
    global _con
    if _con is not None:
        return _con
    import duckdb

    tmp = os.environ.get("TMPDIR", "/tmp")
    ext_dir = os.environ.get("DUCKDB_EXTENSION_DIR") or (BUNDLED_EXTENSIONS if Path(BUNDLED_EXTENSIONS).is_dir()
                                                         else None)
    config = {"memory_limit": os.environ.get("EMBEDDINGS_MEMORY_LIMIT", "1GB"), "threads": 4,
              "home_directory": tmp, "temp_directory": str(Path(tmp) / "duckdb-spill")}
    if ext_dir:
        config.update(extension_directory=ext_dir, autoinstall_known_extensions=False)
    con = duckdb.connect(config=config)
    con.execute("SET enable_progress_bar = false")  # a session option: it can't go in connect()'s config
    if _root().startswith("s3://"):
        try:
            con.execute("LOAD httpfs")
        except duckdb.Error:
            if ext_dir:
                raise ToolError(f"httpfs extension not installed in {ext_dir} (image built without it)") from None
            try:
                con.execute("INSTALL httpfs")
                con.execute("LOAD httpfs")
            except duckdb.Error as exc:
                raise ToolError("could not install the DuckDB httpfs extension from extensions.duckdb.org: "
                                f"{str(exc)[:200]}. Install it once with network, or set DUCKDB_EXTENSION_DIR.") from None
        con.execute("CREATE SECRET (TYPE s3, PROVIDER config, KEY_ID '', SECRET '', REGION 'us-west-2', "
                    "URL_STYLE 'path')")
        con.execute("SET http_timeout = 30")
        con.execute("SET http_retries = 1")
    _con = con
    return con


def _query(sql: str, params: dict) -> list[tuple]:
    """Run one statement with the interrupt timer (approximate: it lands once the in-flight HTTP read completes)."""
    import duckdb

    con = _connection()
    timer = threading.Timer(_timeout_s(), con.interrupt)
    timer.start()
    try:
        return con.execute(sql, params).fetchall()
    except duckdb.InterruptException:
        raise ToolError(f"Query took longer than {_timeout_s():g}s and was stopped. Try a smaller box.") from None
    except duckdb.Error as exc:
        raise ToolError(f"Embeddings query failed: {type(exc).__name__}: {str(exc)[:300]}") from None
    finally:
        timer.cancel()


def _centre_in(alias: str = "") -> str:
    p = f"{alias}." if alias else ""
    return (f"({p}bbox.xmin + {p}bbox.xmax) / 2 BETWEEN $w AND $e "
            f"AND ({p}bbox.ymin + {p}bbox.ymax) / 2 BETWEEN $s AND $n")


def _bounds(box: list[float]) -> dict:
    return dict(zip(("w", "s", "e", "n"), box))


def _partitions_for(box: list[float]) -> list[str]:
    parts = partitions(box)
    if len(parts) > MAX_PARTITIONS:
        raise ToolError(f"This box touches {len(parts)} geohash partitions ({', '.join(parts)}); at most "
                        f"{MAX_PARTITIONS} are read per month. Move or shrink the box.")
    return parts


# --- tools ---------------------------------------------------------------------------------------------------------

@server.tool(structured_output=False)
def embedding_coverage(bbox: list[float]) -> str:
    """Which months have Clay v1.5 embeddings (Sentinel-2, 1280 m cells, 256 dims) for this area, bbox
    [west, south, east, north] in degrees (at most 10 x 10). Call this first: some months are missing."""
    box = _check_bbox(bbox, MAX_COVERAGE_DEG)
    started = time.monotonic()
    per, not_listed = {}, []
    for g in partitions(box):
        if time.monotonic() - started > COVERAGE_BUDGET_S:
            not_listed.append(g)
            continue
        months = _months(g)
        per[g] = {"first": months[0] if months else None, "last": months[-1] if months else None,
                  "months_available": len(months), "missing": _gaps(months)}
    out: dict = {"dataset": DATASET, "root": _root(), "partitions": list(per)}
    firsts = [p["first"] for p in per.values() if p["first"]]
    lasts = [p["last"] for p in per.values() if p["last"]]
    out["first"] = min(firsts) if firsts else None
    out["last"] = max(lasts) if lasts else None
    out["months_available"] = min((p["months_available"] for p in per.values()), default=0)
    out["missing"] = sorted({m for p in per.values() for m in p["missing"]})
    if len({json.dumps(p, sort_keys=True) for p in per.values()}) > 1:
        out["per_partition"] = per
    if not_listed:
        out["not_listed"] = not_listed
    if not firsts:
        out["note"] = "No embeddings found for this area (it may be open sea or outside coverage)."
    return json.dumps(out)


@server.tool(structured_output=False)
def similar_places(lon: float, lat: float, month: str, search_bbox: list[float], k: int = 10) -> str:
    """Find the k cells inside search_bbox [west, south, east, north] (at most 1 x 1 degree, and containing the
    point) whose embeddings are most similar to the cell containing (lon, lat) in month "YYYY-MM". Use it to find
    places that look like a known example. Returns each cell's bbox, similarity and Sentinel-2 stac_item_id."""
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ToolError(f"({lon}, {lat}) is not a longitude/latitude in degrees")
    box = _check_bbox(search_bbox, MAX_BOX_DEG, "search_bbox")
    if not (box[0] <= lon <= box[2] and box[1] <= lat <= box[3]):
        raise ToolError("The point (lon, lat) must be inside search_bbox.")
    if not 1 <= int(k) <= MAX_K:
        raise ToolError(f"k must be between 1 and {MAX_K}.")
    year, mon = _check_month(month)
    with _lock:
        files = _files(_partitions_for(box), year, mon)
        params = {"files": files, "lon": lon, "lat": lat, **_bounds(box)}
        _query(f"""CREATE OR REPLACE TEMP TABLE sel AS
            SELECT cell_id, stac_item_id, strftime(datetime, '%Y-%m-%d') AS date, bbox,
                   embedding::FLOAT[{DIMS}] AS e
            FROM read_parquet($files)
            WHERE ({_centre_in()})
               OR (bbox.xmin <= $lon AND bbox.xmax >= $lon AND bbox.ymin <= $lat AND bbox.ymax >= $lat)""", params)
        q = _query("""SELECT cell_id, stac_item_id, date, bbox, e FROM sel
            WHERE bbox.xmin <= $lon AND bbox.xmax >= $lon AND bbox.ymin <= $lat AND bbox.ymax >= $lat LIMIT 1""",
                   {"lon": lon, "lat": lat})
        if not q:
            raise ToolError(f"No embedding cell contains ({lon}, {lat}) in {month}. The point may be over the sea or "
                            "outside coverage.")
        qid, qscene, qdate, qbox, qe = q[0]
        rows = _query(f"""SELECT cell_id, array_cosine_similarity(e, $qe::FLOAT[{DIMS}]) AS sim, bbox, stac_item_id,
                   date FROM sel WHERE cell_id <> $qid AND {_centre_in()} ORDER BY sim DESC LIMIT $k""",
                      {"qe": qe, "qid": qid, "k": int(k), **_bounds(box)})
        searched = _query(f"SELECT count(*) FROM sel WHERE cell_id <> $qid AND {_centre_in()}",
                          {"qid": qid, **_bounds(box)})[0][0]
    return json.dumps({
        "month": month,
        "query": {"cell_id": qid, "bbox": _box(qbox), "stac_item_id": qscene, "date": qdate},
        "results": [{"cell_id": c, "similarity": _r(sim, 3), "bbox": _box(b), "stac_item_id": sc, "date": d}
                    for c, sim, b, sc, d in rows],
        "searched_cells": searched,
    })


@server.tool(structured_output=False)
def embedding_change(bbox: list[float], from_month: str, to_month: str) -> str:
    """Compare each cell in bbox [west, south, east, north] (at most 1 x 1 degree) between two months ("YYYY-MM").
    Low cosine similarity means the surface changed. Returns summary statistics, the 10 most-changed cells, and the
    Sentinel-2 scene ids (stac_item_id) used for each month."""
    box = _check_bbox(bbox, MAX_BOX_DEG)
    fy, fm = _check_month(from_month, "from_month")
    ty, tm = _check_month(to_month, "to_month")
    with _lock:
        parts = _partitions_for(box)
        params = {"a": _files(parts, fy, fm), "b": _files(parts, ty, tm), **_bounds(box)}
        rows = _query(f"""
            WITH a AS (SELECT cell_id, stac_item_id, bbox, embedding::FLOAT[{DIMS}] AS e
                       FROM read_parquet($a) WHERE {_centre_in()}),
                 b AS (SELECT cell_id, stac_item_id, embedding::FLOAT[{DIMS}] AS e
                       FROM read_parquet($b) WHERE {_centre_in()})
            SELECT a.cell_id, array_cosine_similarity(a.e, b.e) AS sim, a.bbox, a.stac_item_id, b.stac_item_id
            FROM a JOIN b USING (cell_id)""", params)
    out: dict = {"from": from_month, "to": to_month, "cells_compared": len(rows)}
    if not rows:
        return json.dumps({**out, "mean_similarity": None, "p10_similarity": None, "most_changed": [],
                           "scenes": {"from": [], "to": []}, "note": "no cells in this box for both months"})
    sims = sorted(r[1] for r in rows)
    p10 = sims[max(0, int(len(sims) * 0.1) - 1)] if len(sims) >= 10 else sims[0]
    lowest = sorted(rows, key=lambda r: r[1])[:MOST_CHANGED]

    def scenes(i: int) -> list[str]:
        seen: list[str] = []
        for r in rows:
            if r[i] not in seen:
                seen.append(r[i])
        return seen[:MAX_SCENES]

    return json.dumps({**out, "mean_similarity": _r(statistics.fmean(sims), 3), "p10_similarity": _r(p10, 3),
                       "most_changed": [{"cell_id": c, "similarity": _r(s, 3), "bbox": _box(b),
                                         "from_stac_item_id": fa, "to_stac_item_id": fb}
                                        for c, s, b, fa, fb in lowest],
                       "scenes": {"from": scenes(3), "to": scenes(4)}})


if __name__ == "__main__":
    server.run("stdio")
