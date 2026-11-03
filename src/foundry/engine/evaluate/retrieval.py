"""Retrieval output scorer (requirement-gaps slice C, ADR-4): how well a caption retrieves its own tile.

Candidates are the target tile plus its `negatives` nearest tiles by Clay v1.5 NAIP embedding (hard look-alikes).
The run's caption and each candidate's reference caption are embedded as text; the target's rank by caption
similarity gives the score 1 / rank (ties count against the caption). Chosen by tasks/<task>/scorer.json.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from foundry.data import samples
from foundry.data.tasks import RubricError, load_scorer, task_dir

VERSION = "retrieval-v1"


class ScorerError(Exception):
    """A malformed scorer.json or reference file: evaluation stops before any model call."""


@dataclass(frozen=True)
class Spec:
    task_id: str
    references_file: Path
    references_id: str | None
    sample_name: str
    sample_id: str | None
    negatives: int
    targets: dict[str, str]


def load(task_id: str) -> Spec | None:
    try:
        raw = load_scorer(task_id)
    except RubricError as exc:
        raise ScorerError(str(exc)) from None
    if raw is None:
        return None
    where = f"tasks/{task_id}/scorer.json"
    if raw["kind"] != "retrieval":
        raise ScorerError(f'{where}: kind {raw["kind"]!r} is not a known scorer (known: "retrieval")')
    try:
        sample = raw["sample"]
        spec = Spec(task_id=task_id, references_file=task_dir(task_id) / raw["references"],
                    references_id=raw.get("references_id"), sample_name=sample["name"],
                    sample_id=sample.get("sample_id"), negatives=int(raw.get("negatives", 7)),
                    targets=dict(raw["targets"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ScorerError(f"{where}: missing or bad field ({exc})") from None
    if not 1 <= spec.negatives <= 63:
        raise ScorerError(f"{where}: negatives must be 1-63")
    return spec


def _references(spec: Spec) -> tuple[dict[str, str], bytes]:
    try:
        raw = spec.references_file.read_bytes()
    except OSError:
        raise ScorerError(f"{spec.references_file}: not found") from None
    refs = {}
    for n, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if line.strip():
            try:
                obj = json.loads(line)
                refs[obj["tile_id"]] = obj["caption"]
            except (ValueError, KeyError, TypeError):
                raise ScorerError(f"{spec.references_file}:{n}: each line must be {{\"tile_id\", \"caption\"}}") from None
    return refs, raw


def _cos(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def candidates(tiles: dict[str, list[float]], target: str, negatives: int) -> list[tuple[str, float]]:
    """Target first (similarity 1.0), then the `negatives` most Clay-similar tiles; ties broken by tile id."""
    others = sorted(((tid, _cos(tiles[target], emb)) for tid, emb in tiles.items() if tid != target),
                    key=lambda x: (-x[1], x[0]))
    return [(target, 1.0), *others[:negatives]]


def rank_of_target(sims: list[float]) -> int:
    """sims[0] is the target's; ties count against the caption."""
    return 1 + sum(1 for s in sims[1:] if s >= sims[0])


def _unscored(error: str, rubric_version: str | None) -> tuple[dict, bytes | None]:
    return {"criteria": [], "score": None, "scorer": VERSION, "judge": None, "rubric_version": rubric_version,
            "error": error}, None


def score(spec: Spec, run, output, embedder, storage_cfg, prompt_text: str | None,
          rubric_version: str | None = None) -> tuple[dict, bytes | None]:
    """(scores.json "output" section, retrieval.json bytes or None when unscored). Errors here mean "unscored"; only
    a malformed task pack (ScorerError) stops the evaluation."""
    refs, refs_raw = _references(spec)
    target = spec.targets.get(run.prompt_id)
    if target is None:
        return _unscored(f"no retrieval target for prompt '{run.prompt_id}' (add it under \"targets\" in "
                         f"tasks/{spec.task_id}/scorer.json)", rubric_version)
    if prompt_text is not None and target not in prompt_text:
        return _unscored(f"prompt {run.prompt_id} names a different tile than scorer.json's target {target}",
                         rubric_version)
    if output.kind != "text" or not output.text.strip():
        return _unscored("no caption", rubric_version)
    if embedder is None:
        return _unscored("no [embedder] in config; the retrieval scorer needs one", rubric_version)
    sample_dir = samples.ensure(spec.sample_name, storage_cfg)
    if sample_dir is None:
        return _unscored(samples.staging_hint(spec.sample_name, storage_cfg), rubric_version)
    tiles_raw = (sample_dir / "tiles.json").read_bytes()
    meta = json.loads(tiles_raw)
    if spec.sample_id and meta.get("sample_id") != spec.sample_id:
        return _unscored(f"sample {spec.sample_name} is {meta.get('sample_id')}, but scorer.json expects "
                         f"{spec.sample_id}: re-stage the sample or update scorer.json", rubric_version)
    tiles = {t["tile_id"]: t["embedding"] for t in meta["tiles"]}
    if target not in tiles:
        return _unscored(f"target {target} is not in sample {spec.sample_name}", rubric_version)
    cands = candidates(tiles, target, spec.negatives)
    missing = [tid for tid, _ in cands if tid not in refs]
    if missing:
        raise ScorerError(f"{spec.references_file}: no reference caption for {', '.join(missing)}")
    try:
        vecs = embedder.embed([output.text] + [refs[tid] for tid, _ in cands])
    except Exception as exc:  # noqa: BLE001 - an embedder failure leaves the output unscored, like a judge failure
        return _unscored(f"embedder {embedder.cfg.id}: {type(exc).__name__}: {str(exc)[:200]}", rubric_version)
    sims = [sum(x * y for x, y in zip(vecs[0], v)) for v in vecs[1:]]
    rank = rank_of_target(sims)
    rows = [{"tile_id": tid, "clay_similarity": round(clay, 4), "caption_similarity": round(sim, 4)}
            for (tid, clay), sim in zip(cands, sims)]
    retrieval = {"target": target, "rank": rank, "of": len(cands), "candidates": rows,
                 "embedder": {"provider": embedder.cfg.provider, "id": embedder.cfg.id},
                 "references_id": spec.references_id,
                 "references_sha256": hashlib.sha256(refs_raw).hexdigest(),
                 "tiles_sha256": hashlib.sha256(tiles_raw).hexdigest(), "sample_id": meta.get("sample_id")}
    section = {"criteria": [{"id": "retrieval", "name": "Retrieval", "score": round(4 / rank, 2), "max": 4,
                             "rationale": f"Target {target} ranked {rank} of {len(cands)} among its Clay-nearest "
                                          f"look-alikes by caption similarity."}],
               "score": round(1 / rank, 4), "scorer": VERSION, "judge": None, "rubric_version": rubric_version,
               "retrieval": retrieval}
    record = {"scorer": VERSION, "target": target, "references_id": spec.references_id, "caption": output.text,
              "sample_id": meta.get("sample_id"),
              "candidates": [{"tile_id": tid, "reference": refs[tid], "clay_similarity": round(clay, 4)}
                             for tid, clay in cands]}
    return section, (json.dumps(record, indent=1) + "\n").encode()
