"""Batch directories under <runs dir>/.batches (increment 16: moved here from ui/app.py, unchanged)."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from foundry.data.runs import float_or_none, is_regular, is_valid_run_id, num, read_json

BATCH_ID_RE = re.compile(r"^b-\d{8}-\d{6}-[a-f0-9]{4}$")
# summary.csv columns the API types as strings (increment 17, R4): always present, "" when missing.
COLUMNS = ("variant", "model", "prompt", "repeat", "status", "model_calls", "tool_calls", "tool_errors", "overall",
           "workflow", "output", "run_dir")
META_TYPES = {"status": str, "jobs": int, "max_cost": (int, float), "spent_usd": (int, float)}


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
            known = {k: r[k] if isinstance(r.get(k), str) else "" for k in COLUMNS}
            rows.append({**r, **known, "link": link, "cost": num(float_or_none(r.get("cost_usd"))),
                         "overall_f": num(float_or_none(r.get("overall")))})
    return rows


def batch_list(batches_dir: Path, runs_dir: Path) -> list[dict]:
    if not batches_dir.is_dir():
        return []
    out = []
    for d in batches_dir.iterdir():
        if not (d.is_dir() and BATCH_ID_RE.match(d.name)):
            continue
        meta = read_meta(d)
        rows = batch_rows(d, runs_dir)
        out.append({"id": d.name, "status": meta.get("status", "starting"), "jobs": meta.get("jobs", len(rows)),
                    "done": sum(r["status"] not in ("pending", "") for r in rows),
                    "spent": sum(r["cost"] for r in rows if isinstance(r["cost"], float)),
                    "max_cost": meta.get("max_cost"), "models": meta.get("models") or meta.get("configs") or []})
    return sorted(out, key=lambda b: b["id"], reverse=True)


def batch_path(batches_dir: Path, batch_id: str) -> Path | None:
    """The directory for a batch id from a URL: a valid id naming a directory, else None."""
    path = batches_dir / batch_id
    return path if BATCH_ID_RE.match(batch_id) and path.is_dir() else None


def read_meta(path: Path) -> dict:
    """batch.json, without the fields the API types when they have the wrong type (increment 17, R4)."""
    meta = read_json(path / "batch.json") or {}
    for k, t in META_TYPES.items():
        if k in meta and (isinstance(meta[k], bool) or not isinstance(meta[k], t)):
            del meta[k]
    for k in ("models", "configs"):
        if k in meta and not (isinstance(meta[k], list) and all(isinstance(x, str) for x in meta[k])):
            del meta[k]
    return meta



def write_meta(path: Path, meta: dict) -> None:
    import json

    path.mkdir(parents=True, exist_ok=True)
    tmp = path / "batch.json.tmp"
    tmp.write_text(json.dumps(meta, indent=1))
    tmp.replace(path / "batch.json")
