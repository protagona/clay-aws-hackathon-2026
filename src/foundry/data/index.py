"""The run index: one TREE item per run (`run#<id>`) carries the board card and a summary next to the tree's version
and manifest, so the board is one query instead of reading every run (aws-deploy design ADR-4, slice 2).

Workers write `card` and `summary` (JSON) with each push of a run; the API only reads them.
"""

from __future__ import annotations

import json

from foundry.data.mirror import PK


def run_items(store) -> list[dict]:
    """Every indexed run: {run_id, version, card, summary}, without manifests (the board's projection)."""
    out = []
    for it in store.query(PK, prefix="run#", attrs=("version", "card", "summary")):
        out.append({"run_id": it["sk"].partition("#")[2], "version": int(it.get("version", 0)),
                    "card": json.loads(it.get("card") or "null"), "summary": json.loads(it.get("summary") or "null")})
    return out


def run_attrs(card: dict, summary: dict) -> dict:
    """TREE attributes to store with a run push (Mirror.push / push_dir `attrs`)."""
    return {"card": json.dumps(card, sort_keys=True), "summary": json.dumps(summary, sort_keys=True)}
