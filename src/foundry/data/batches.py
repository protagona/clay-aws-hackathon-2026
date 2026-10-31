"""Batch directories under <runs dir>/.batches (increment 16: moved here from ui/app.py, unchanged)."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from foundry.data.runs import float_or_none, is_regular, is_valid_run_id, num, read_json

BATCH_ID_RE = re.compile(r"^b-\d{8}-\d{6}-[a-f0-9]{4}$")


def batch_rows(path: Path, runs_dir: Path) -> list[dict]:
    """summary.csv rows; `link` names the run directory when it exists under runs_dir."""
    f = path / "summary.csv"
    if not is_regular(f):
        return []
    rows = []
    with open(f, newline="") as fh:
        for r in csv.DictReader(fh):
            run_dir = Path(r.get("run_dir") or "")
            link = run_dir.name if r.get("run_dir") and (runs_dir / run_dir.name).is_dir() \
                and is_valid_run_id(run_dir.name) else None
            rows.append({**r, "link": link, "cost": num(float_or_none(r.get("cost_usd"))),
                         "overall_f": num(float_or_none(r.get("overall")))})
    return rows


def batch_list(batches_dir: Path, runs_dir: Path) -> list[dict]:
    if not batches_dir.is_dir():
        return []
    out = []
    for d in batches_dir.iterdir():
        if not (d.is_dir() and BATCH_ID_RE.match(d.name)):
            continue
        meta = read_json(d / "batch.json") or {}
        rows = batch_rows(d, runs_dir)
        out.append({"id": d.name, "status": meta.get("status", "starting"), "jobs": meta.get("jobs", len(rows)),
                    "done": sum(r["status"] not in ("pending", "") for r in rows),
                    "spent": sum(r["cost"] for r in rows if isinstance(r["cost"], float)),
                    "max_cost": meta.get("max_cost"), "models": meta.get("models") or meta.get("configs") or []})
    return sorted(out, key=lambda b: b["id"], reverse=True)
