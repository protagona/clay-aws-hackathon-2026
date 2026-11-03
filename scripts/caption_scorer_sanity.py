"""Caption scorer sanity check (requirement-gaps AC13): does the retrieval scorer rank good captions above bad ones?

For each of the caption task's 12 targets, scores four captions with the configured text embedder:
  own       the target's own reference caption              (want 1.0 on all 12)
  generic   "An aerial image of land."                       (want <= 0.5 on at least 10 of 12)
  other     a seeded-random other tile's reference caption   (want below `own` on at least 10 of 12)
  stuffed   every land-cover word in the references, listed  (recorded only: a gaming attempt)

Usage: AWS_PROFILE=<p> uv run python scripts/caption_scorer_sanity.py [--config config/bedrock-captions.toml] [--md out.md]
Needs the staged sample (samples/naip-captions/, FOUNDRY_SAMPLES_DIR, or the runs bucket). Exit 0 if every check passes.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from types import SimpleNamespace

from foundry.config import load_eval_config, project_root
from foundry.contract import Output
from foundry.engine.evaluate import retrieval
from foundry.engine.models.embedder import build_embedder

GENERIC = "An aerial image of land."
LAND_WORDS = ("houses buildings warehouse silos substation park baseball road highway interchange bridge ramp river "
              "riparian trees shrubs scrub desert sand bare ground field plowed irrigated crop green brown canal ditch "
              "farmstead yard parking lot commercial residential industrial salvage vehicles")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/bedrock-captions.toml")
    ap.add_argument("--md")
    args = ap.parse_args(argv)
    cfg = load_eval_config(args.config)
    if cfg.embedder is None:
        print(f"no [embedder] in {args.config}", file=sys.stderr)
        return 2
    spec = retrieval.load("captions")
    refs = {json.loads(line)["tile_id"]: json.loads(line)["caption"]
            for line in (project_root() / "tasks/captions/references.jsonl").read_text().splitlines() if line.strip()}
    rng = random.Random(12)
    stuffed = " ".join(sorted(set(re.findall(r"[a-z]+", LAND_WORDS))))
    rows, embedder = [], build_embedder(cfg.embedder)

    def one(pid: str, text: str) -> float | None:
        section, _ = retrieval.score(spec, SimpleNamespace(prompt_id=pid), Output(kind="text", text=text), embedder,
                                     cfg.storage, None)
        if section["score"] is None:
            raise SystemExit(f"{pid}: unscored: {section.get('error')}")
        return section["score"]

    for pid, target in sorted(spec.targets.items()):
        other_tile = rng.choice(sorted(t for t in refs if t != target))
        rows.append({"prompt": pid, "target": target, "own": one(pid, refs[target]), "generic": one(pid, GENERIC),
                     "other": one(pid, refs[other_tile]), "other_tile": other_tile, "stuffed": one(pid, stuffed)})
    checks = [
        ("own reference scores 1.0 on all 12", all(r["own"] == 1.0 for r in rows)),
        ("generic caption <= 0.5 on at least 10 of 12", sum(r["generic"] <= 0.5 for r in rows) >= 10),
        ("other tile's reference below own on at least 10 of 12", sum(r["other"] < r["own"] for r in rows) >= 10),
    ]
    lines = [f"# Caption scorer sanity ({cfg.embedder.provider} {cfg.embedder.id}, sample {spec.sample_id})", "",
             "| Prompt | Target | Own | Generic | Other (tile) | Keyword-stuffed |", "|---|---|---|---|---|---|"]
    lines += [f"| {r['prompt']} | {r['target']} | {r['own']:.2f} | {r['generic']:.2f} | {r['other']:.2f} "
              f"({r['other_tile']}) | {r['stuffed']:.2f} |" for r in rows]
    lines += ["", f"Keyword-stuffed mean: {sum(r['stuffed'] for r in rows) / len(rows):.2f} (recorded, not checked)", ""]
    lines += [f"- {'PASS' if ok else 'FAIL'}: {name}" for name, ok in checks]
    text = "\n".join(lines) + "\n"
    print(text)
    if args.md:
        with open(args.md, "w") as f:
            f.write(text)
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
