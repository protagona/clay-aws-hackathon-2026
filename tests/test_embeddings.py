"""Embeddings MCP server (clay-embeddings spec AC9-AC14): against a small local partition with the real layout.

Tests that download real embeddings (~330 MB per partition-month) run only with FOUNDRY_LIVE_EMBEDDINGS=1.
"""

import asyncio
import json
import math
import os
import random
import sys
import tomllib

import pytest
from conftest import ROOT

from foundry.engine.mcp_servers import embeddings as emb

DIMS = 256
STEP = 0.0115  # ~1.28 km, like the real cells
W0, S0, N_SIDE = -120.5, 36.9, 20  # a 20 x 20 grid of cells around (-120.385, 37.015), all in geohash 9q
POINT = (W0 + 4.5 * STEP, S0 + 4.5 * STEP)  # inside cell (4, 4), a "farmland" cell
CHANGED = (12, 7)


def _vec(seed: int, base: list[float] | None = None, noise: float = 0.0) -> list[float]:
    rnd = random.Random(seed)
    v = [rnd.gauss(0, 1) for _ in range(DIMS)] if base is None else [b + rnd.gauss(0, noise) for b in base]
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


def _write(root, year, month, *, changed=False):
    import duckdb

    farm = _vec(1)
    rows = []
    for i in range(N_SIDE):
        for j in range(N_SIDE):
            xmin, ymin = W0 + i * STEP, S0 + j * STEP
            if changed and (i, j) == CHANGED:
                e = _vec(999)  # unrelated: the surface changed
            elif (i + j) % 4 == 0:
                e = _vec(10_000 + i * 100 + j, farm, 0.01)  # "farmland": close to the query cell's vector
            else:
                e = _vec(20_000 + i * 100 + j)
            rows.append((f"c{i:02d}{j:02d}", f"S2A_10SFF_{year}{month:02d}02_0_L2A", f"{year}-{month:02d}-02 18:00:00",
                         e, xmin, ymin, xmin + STEP, ymin + STEP))
    folder = root / "geohash=9q" / f"year={year}" / f"month={month:02d}"
    folder.mkdir(parents=True)
    con = duckdb.connect()
    con.execute("CREATE TABLE t (cell_id VARCHAR, stac_item_id VARCHAR, datetime TIMESTAMPTZ, embedding FLOAT[], "
                "xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)")
    con.executemany("INSERT INTO t VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.execute(f"""COPY (SELECT cell_id, stac_item_id, datetime, embedding,
                         {{'xmin': xmin, 'ymin': ymin, 'xmax': xmax, 'ymax': ymax}} AS bbox FROM t)
                    TO '{folder / "abc123.parquet"}' (FORMAT parquet)""")


@pytest.fixture(scope="module")
def _local_data(tmp_path_factory):
    root = tmp_path_factory.mktemp("emb")
    _write(root, 2020, 7)
    _write(root, 2024, 5)
    _write(root, 2024, 7, changed=True)
    return root


@pytest.fixture
def local_root(_local_data, monkeypatch):
    root = _local_data
    monkeypatch.setenv("EMBEDDINGS_ROOT", str(root))
    monkeypatch.setattr(emb, "_con", None)
    yield root
    if emb._con is not None:
        emb._con.close()
    monkeypatch.setattr(emb, "_con", None)


BOX = [W0, S0, W0 + N_SIDE * STEP, S0 + N_SIDE * STEP]


def test_geohash_and_partitions():
    assert emb.geohash2(37.0, -120.4) == "9q" and emb.geohash2(27.05, 85.3) == "tu"
    assert emb.partitions([-120.5, 36.9, -120.0, 37.4]) == ["9q"]
    assert sorted(emb.partitions([-112.9, 36.0, -112.1, 36.5])) == ["9q", "9w"]  # crosses -112.5
    assert len(emb.partitions([-125.0, 30.0, -115.0, 40.0])) == 6  # a 10-degree coverage box


def test_coverage_lists_months_and_gaps(local_root):  # AC10
    out = json.loads(emb.embedding_coverage(BOX))
    assert out["partitions"] == ["9q"] and out["first"] == "2020-07" and out["last"] == "2024-07"
    assert "2024-06" in out["missing"] and "2020-08" in out["missing"] and "2024-05" not in out["missing"]
    assert out["months_available"] == 3 and "Clay v1.5" in out["dataset"]


def test_similar_places_ranks_lookalikes(local_root):  # AC11
    out = json.loads(emb.similar_places(POINT[0], POINT[1], "2024-07", BOX, k=5))
    assert out["query"]["cell_id"] == "c0404" and out["query"]["stac_item_id"] == "S2A_10SFF_20240702_0_L2A"
    assert out["query"]["date"] == "2024-07-02"
    ids = [r["cell_id"] for r in out["results"]]
    assert len(ids) == 5 and "c0404" not in ids
    assert all((int(c[1:3]) + int(c[3:5])) % 4 == 0 for c in ids)  # every hit is "farmland"
    sims = [r["similarity"] for r in out["results"]]
    assert sims == sorted(sims, reverse=True) and sims[0] > 0.9
    assert out["searched_cells"] == N_SIDE * N_SIDE - 1
    assert set(out["results"][0]) == {"cell_id", "similarity", "bbox", "stac_item_id", "date"}


def test_embedding_change_finds_the_changed_cell(local_root):  # AC12
    out = json.loads(emb.embedding_change(BOX, "2024-05", "2024-07"))
    assert out["cells_compared"] == N_SIDE * N_SIDE
    assert out["most_changed"][0]["cell_id"] == f"c{CHANGED[0]:02d}{CHANGED[1]:02d}"
    assert out["most_changed"][0]["similarity"] < 0.5 and len(out["most_changed"]) == 10
    assert 0.9 < out["mean_similarity"] < 1 and out["most_changed"][0]["similarity"] <= out["p10_similarity"] <= 1
    assert out["scenes"] == {"from": ["S2A_10SFF_20240502_0_L2A"], "to": ["S2A_10SFF_20240702_0_L2A"]}
    assert set(out["most_changed"][0]) == {"cell_id", "similarity", "bbox", "from_stac_item_id", "to_stac_item_id"}


def test_change_over_empty_area(local_root):
    out = json.loads(emb.embedding_change([-121.5, 37.6, -121.2, 37.9], "2024-05", "2024-07"))
    assert out["cells_compared"] == 0 and out["mean_similarity"] is None and out["most_changed"] == []
    assert out["note"] == "no cells in this box for both months"


@pytest.mark.parametrize("call,needle", [
    (lambda: emb.embedding_change([-121.6, 36.9, -120.0, 37.3], "2024-05", "2024-07"), "the limit is 1° x 1°"),
    (lambda: emb.embedding_change(BOX, "2024-06", "2024-07"), "Nearest months with data: 2024-05, 2024-07"),
    (lambda: emb.embedding_change(BOX, "June 2024", "2024-07"), 'from_month must be "YYYY-MM"'),
    (lambda: emb.similar_places(-121.4, 37.8, "2024-07", [-121.5, 37.7, -121.2, 37.9]), "No embedding cell contains"),
    (lambda: emb.similar_places(-119.0, 37.0, "2024-07", BOX), "must be inside search_bbox"),
    (lambda: emb.similar_places(POINT[0], POINT[1], "2024-07", BOX, k=26), "k must be between 1 and 25"),
    (lambda: emb.similar_places(500, 37.0, "2024-07", BOX), "is not a longitude/latitude"),
    (lambda: emb.embedding_change([-112.9, 36.0, -112.1, 36.5], "2024-05", "2024-07"), "but not 9w"),
    (lambda: emb.embedding_coverage([-130.0, 30.0, -115.0, 33.0]), "Coverage box spans 15° x 3°"),
    (lambda: emb.embedding_coverage([1, 0, 0, 1]), "west<east"),
])
def test_errors(local_root, call, needle):  # AC13
    with pytest.raises(emb.ToolError, match=None) as exc:
        call()
    assert needle in str(exc.value)


def test_four_partitions_refused():  # AC13 (no data needed: refused before any read)
    with pytest.raises(emb.ToolError, match="touches 4 geohash partitions"):
        emb._partitions_for([-113.0, 39.0, -112.0, 39.9])  # corner of 9q / 9r / 9w / 9x


def test_result_size_worst_case(local_root):  # AC14
    out = emb.similar_places(POINT[0], POINT[1], "2024-07", BOX, k=25)
    change = emb.embedding_change(BOX, "2020-07", "2024-07")
    assert len(out) < 8000 and len(change) < 8000


def test_local_root_needs_no_httpfs(local_root):  # design: tests never download an extension
    emb.embedding_change(BOX, "2024-05", "2024-07")
    loaded = emb._con.execute("SELECT loaded FROM duckdb_extensions() WHERE extension_name = 'httpfs'").fetchone()
    assert loaded in (None, (False,))


def test_registered_everywhere_stac_is():  # AC9
    for f in sorted((ROOT / "config").glob("*.toml")):
        servers = tomllib.loads(f.read_text()).get("mcp", {}).get("servers", {})
        if "stac" in servers:
            assert servers["embeddings"]["args"] == ["-m", "foundry.engine.mcp_servers.embeddings"], f.name


def test_stdio_lists_three_tools(local_root):  # AC9, the real entry point
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def go():
        p = StdioServerParameters(command=sys.executable, args=["-m", "foundry.engine.mcp_servers.embeddings"],
                                  env={"EMBEDDINGS_ROOT": str(local_root), "PATH": os.environ.get("PATH", "")})
        async with stdio_client(p) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = sorted(t.name for t in (await s.list_tools()).tools)
            bad = await s.call_tool("similar_places", {"lon": 0, "lat": 0, "month": "2024-07",
                                                       "search_bbox": [1, 1, 2, 2]})
            ok = await s.call_tool("embedding_coverage", {"bbox": BOX})
            return names, bad, ok

    names, bad, ok = asyncio.run(go())
    assert names == ["embedding_change", "embedding_coverage", "similar_places"]
    assert bad.is_error and "inside search_bbox" in bad.content[0].text
    assert json.loads(ok.content[0].text)["last"] == "2024-07"


LIVE = pytest.mark.skipif(os.environ.get("FOUNDRY_LIVE_EMBEDDINGS") != "1",
                          reason="downloads real embeddings; set FOUNDRY_LIVE_EMBEDDINGS=1")


@pytest.fixture
def real_root(monkeypatch):
    monkeypatch.delenv("EMBEDDINGS_ROOT", raising=False)
    monkeypatch.setattr(emb, "_con", None)
    yield
    monkeypatch.setattr(emb, "_con", None)


FRESNO = [-120.0, 36.6, -119.6, 36.9]


@pytest.mark.network
def test_live_coverage(real_root):  # AC10 against the real bucket: an S3 listing only, cheap
    out = json.loads(emb.embedding_coverage(FRESNO))
    assert out["partitions"] == ["9q"] and out["first"] == "2017-01" and "2024-06" in out["missing"]


@LIVE
@pytest.mark.network
def test_live_similar_and_change(real_root):  # AC11, AC12 against the real bucket (downloads ~1 GB)
    sim = json.loads(emb.similar_places(-119.8, 36.75, "2024-07", FRESNO, k=5))
    assert len(sim["results"]) == 5 and sim["query"]["stac_item_id"].startswith("S2")
    change = json.loads(emb.embedding_change(FRESNO, "2020-07", "2024-07"))
    assert change["cells_compared"] > 100 and change["most_changed"]
