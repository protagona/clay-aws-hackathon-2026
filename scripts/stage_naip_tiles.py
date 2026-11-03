"""Stage the caption task's NAIP sample (requirement-gaps spec AC10b): one 8x8-tile crop of a NAIP image and the
matching Clay v1.5 NAIP embeddings, written locally, then uploaded to a deployment's runs bucket.

  # 1. read NAIP once (Requester Pays: a few windowed reads, billed to the caller's account) and write locally
  AWS_PROFILE=<p> uv run python scripts/stage_naip_tiles.py --cog 32115/m_3211513_se_11_060_20200524 --row0 0 --col0 27 \
      [--sheets DIR]
  # 2. copy the local files to a named deployment's runs bucket (no NAIP reads)
  AWS_PROFILE=<p> uv run python scripts/stage_naip_tiles.py --upload embeddings

Local output: samples/naip-captions/{sample.tif,tiles.json} (samples/ is git-ignored). Bucket: samples/naip-captions/.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCAL = REPO / "samples" / "naip-captions"
NAME = "naip-captions"
TILE = 256
SIZE = 8  # tiles per side
NAIP = "s3://naip-analytic/ca/2020/60cm/rgbir_cog/{cog}.tif"
EMB = "s3://us-west-2.opendata.source.coop/clay/clay-v1-5-naip-2/ca/2020/60cm/rgbir_cog/{cog}.parquet"
REGION = "us-west-2"


def tile_id(row: int, col: int) -> str:
    return f"t-r{row:02d}-c{col:02d}"


def source_index(row0: int, col0: int, row: int, col: int, ncols: int) -> int:
    """Row-major index of a 256-px tile in the source image's tile grid (verified against clay-v1-5-naip-2, which has
    one row per tile, ncols * nrows rows, from the north-west)."""
    return (row0 + row) * ncols + (col0 + col)


def check_grid(width: int, height: int, rows_in_parquet: int) -> tuple[int, int]:
    ncols, nrows = width // TILE, height // TILE
    if ncols * nrows != rows_in_parquet:
        raise SystemExit(f"tile grid {ncols}x{nrows}={ncols * nrows} != {rows_in_parquet} embedding rows; "
                         "this image's embeddings don't follow the expected layout")
    return ncols, nrows


def stage(cog: str, row0: int, col0: int, sheets: Path | None) -> None:
    import duckdb
    import numpy as np
    import rasterio
    from rasterio.windows import Window

    os.environ.setdefault("AWS_REQUEST_PAYER", "requester")
    os.environ.setdefault("AWS_REGION", REGION)
    LOCAL.mkdir(parents=True, exist_ok=True)
    src_url = NAIP.format(cog=cog)
    with rasterio.open(src_url) as src:
        width, height = src.width, src.height
        win = Window(col0 * TILE, row0 * TILE, SIZE * TILE, SIZE * TILE)
        rgb = src.read([1, 2, 3], window=win)
        profile = {"driver": "GTiff", "width": SIZE * TILE, "height": SIZE * TILE, "count": 3, "dtype": "uint8",
                   "crs": src.crs, "transform": src.window_transform(win), "compress": "JPEG", "jpeg_quality": 90,
                   "photometric": "YCbCr", "tiled": True, "blockxsize": TILE, "blockysize": TILE}
    with rasterio.open(LOCAL / "sample.tif", "w", **profile) as dst:
        dst.write(rgb)

    con = duckdb.connect()
    con.sql("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial;")
    con.sql(f"CREATE SECRET (TYPE s3, PROVIDER config, REGION '{REGION}', URL_STYLE 'path')")
    emb_url = EMB.format(cog=cog)
    rows = con.execute(f"SELECT file_row_number AS i, ST_XMin(geometry), ST_YMin(geometry), ST_XMax(geometry), "
                       f"ST_YMax(geometry), embeddings FROM read_parquet('{emb_url}', file_row_number=true) "
                       f"ORDER BY file_row_number").fetchall()
    ncols, _ = check_grid(width, height, len(rows))
    by_index = {r[0]: r for r in rows}
    from rasterio.warp import transform_bounds
    from rasterio.windows import bounds as window_bounds
    with rasterio.open(LOCAL / "sample.tif") as crop:
        crs, transform = crop.crs, crop.transform
    tiles = []
    for row in range(SIZE):
        for col in range(SIZE):
            i = source_index(row0, col0, row, col, ncols)
            _, w, s, e, n, emb = by_index[i]
            px = transform_bounds(crs, "EPSG:4326",
                                  *window_bounds(Window(col * TILE, row * TILE, TILE, TILE), transform))
            off = max(abs(a - b) for a, b in zip(px, (w, s, e, n)))
            if off > 1e-5:
                raise SystemExit(f"{tile_id(row, col)}: embedding footprint is {off:.2e} deg from the pixel window; "
                                 "the row order or CRS isn't what this script assumes")
            tiles.append({"tile_id": tile_id(row, col), "row": row, "col": col,
                          "bbox": [round(w, 6), round(s, 6), round(e, 6), round(n, 6)],
                          "embedding": [round(float(x), 5) for x in emb]})
    staged_at = datetime.now(timezone.utc)
    meta = {"sample_id": f"{cog.rsplit('/', 1)[-1]}-r{row0}-c{col0}-{staged_at:%Y%m%dT%H%M%SZ}",
            "source": {"cog": src_url, "embeddings": emb_url, "acquired": _acquired(cog),
                       "window": {"row0": row0, "col0": col0, "size": SIZE, "tile_px": TILE},
                       "staged_at": staged_at.isoformat(timespec="seconds"),
                       "license": "NAIP: public domain (USDA FSA); Clay embeddings: CC BY 4.0"},
            "tiles": tiles}
    (LOCAL / "tiles.json").write_text(json.dumps(meta))
    print(f"wrote {LOCAL / 'sample.tif'} ({(LOCAL / 'sample.tif').stat().st_size} bytes) and "
          f"{LOCAL / 'tiles.json'} ({len(tiles)} tiles, sample_id {meta['sample_id']})")
    if sheets:
        _sheets(np.moveaxis(rgb, 0, -1), sheets)


def _acquired(cog: str) -> str:
    d = cog.rsplit("_", 1)[-1]
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def _sheets(img, out: Path) -> None:
    from PIL import Image, ImageDraw

    out.mkdir(parents=True, exist_ok=True)
    for qr in range(0, SIZE, 4):
        for qc in range(0, SIZE, 4):
            sheet = Image.new("RGB", (4 * (TILE + 8), 4 * (TILE + 24)), "white")
            draw = ImageDraw.Draw(sheet)
            for r in range(4):
                for c in range(4):
                    row, col = qr + r, qc + c
                    tile = img[row * TILE:(row + 1) * TILE, col * TILE:(col + 1) * TILE]
                    x, y = c * (TILE + 8), r * (TILE + 24)
                    sheet.paste(Image.fromarray(tile), (x, y + 20))
                    draw.text((x + 2, y + 4), tile_id(row, col), fill="black")
            sheet.save(out / f"sheet-r{qr}-c{qc}.png")
    print(f"contact sheets in {out}")


def bucket_for(deployment: str) -> str:
    """A named deployment's runs bucket: the Bucket output of Foundry-<name>-Data (as scripts/cloud.sh reads it)."""
    import boto3

    cfn = boto3.client("cloudformation", region_name=REGION)
    stack = cfn.describe_stacks(StackName=f"Foundry-{deployment}-Data")["Stacks"][0]
    for out in stack.get("Outputs", []):
        if out["OutputKey"] == "Bucket":
            return out["OutputValue"]
    raise SystemExit(f"no Bucket output on stack Foundry-{deployment}-Data")


def upload(target: str) -> None:
    import boto3

    files = [LOCAL / "sample.tif", LOCAL / "tiles.json"]
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise SystemExit(f"not staged locally: {', '.join(missing)} (run with --cog first)")
    bucket = bucket_for(target)
    s3 = boto3.client("s3", region_name=REGION)
    for f in files:
        s3.upload_file(str(f), bucket, f"samples/{NAME}/{f.name}")
        print(f"uploaded s3://{bucket}/samples/{NAME}/{f.name}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cog", help="<quad>/<NAIP image name> under naip-analytic/ca/2020/60cm/rgbir_cog/")
    ap.add_argument("--row0", type=int, default=0)
    ap.add_argument("--col0", type=int, default=0)
    ap.add_argument("--sheets", type=Path, help="write 4x4 contact sheets here")
    ap.add_argument("--upload", metavar="DEPLOYMENT", help="a named deployment, e.g. embeddings")
    args = ap.parse_args(argv)
    if not args.cog and not args.upload:
        ap.error("give --cog to stage, --upload to copy to a bucket, or both")
    if args.cog:
        stage(args.cog, args.row0, args.col0, args.sheets)
    if args.upload:
        upload(args.upload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
