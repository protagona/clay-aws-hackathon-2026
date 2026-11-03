"""A synthetic 8x8-tile NAIP caption sample (requirement-gaps design: synthetic sample for tests and offline runs).

Solid-colour 256 px tiles in EPSG:26911 with footprints that match the pixel windows (as the real staging script
checks), random unit Clay-like embeddings, and by default the caption task's real sample_id so its scorer.json
accepts it.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def scorer_sample_id() -> str:
    return json.loads((ROOT / "tasks" / "captions" / "scorer.json").read_text())["sample"]["sample_id"]


def make_sample(d: Path, seed: int = 0, sample_id: str | None = None) -> Path:
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window, bounds

    d.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    transform = from_origin(632064.0, 3632388.0, 0.6, 0.6)
    img = np.zeros((3, 2048, 2048), dtype="uint8")
    tiles = []
    for row in range(8):
        for col in range(8):
            colour = [rng.randrange(256) for _ in range(3)]
            for b in range(3):
                img[b, row * 256:(row + 1) * 256, col * 256:(col + 1) * 256] = colour[b]
            bbox = transform_bounds("EPSG:26911", "EPSG:4326", *bounds(Window(col * 256, row * 256, 256, 256), transform))
            emb = [rng.gauss(0, 1) for _ in range(16)]
            tiles.append({"tile_id": f"t-r{row:02d}-c{col:02d}", "row": row, "col": col,
                          "bbox": [round(x, 6) for x in bbox], "embedding": emb})
    with rasterio.open(d / "sample.tif", "w", driver="GTiff", width=2048, height=2048, count=3, dtype="uint8",
                       crs="EPSG:26911", transform=transform) as dst:
        dst.write(img)
    meta = {"sample_id": sample_id or scorer_sample_id(),
            "source": {"cog": "s3://naip-analytic/test/synthetic.tif", "acquired": "2020-05-24"}, "tiles": tiles}
    (d / "tiles.json").write_text(json.dumps(meta))
    return d
