"""The local spend ledger (a laptop with a cap set; aws-deploy spec A5): one JSON line per started job, charged its
reservation, under <runs dir>/.spend.jsonl. The cloud keeps its ledger in DynamoDB (services.cloud_jobs)."""

from __future__ import annotations

import json
from pathlib import Path

NAME = ".spend.jsonl"


def month_total(runs_dir: Path, month: str) -> float:
    f = Path(runs_dir) / NAME
    if not f.is_file():
        return 0.0
    total = 0.0
    for line in f.read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("month") == month and isinstance(e.get("usd"), (int, float)):
            total += e["usd"]
    return total


def charge(runs_dir: Path, month: str, job_id: str, usd: float) -> None:
    f = Path(runs_dir) / NAME
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a") as fh:
        fh.write(json.dumps({"month": month, "job": job_id, "usd": usd}) + "\n")
