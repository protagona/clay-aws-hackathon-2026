"""Command-line entry point: foundry run | evaluate | validate | ui."""

from __future__ import annotations

import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="foundry", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the agent on one prompt and record a trace")
    run.add_argument("--config", required=True)
    run.add_argument("--prompt", required=True)
    run.add_argument("--runs-dir", default="runs")
    run.add_argument("--run-id")

    ev = sub.add_parser("evaluate", help="summarize and score a recorded run")
    ev.add_argument("run_dir")
    ev.add_argument("--config", default="config/offline.toml")
    ev.add_argument("--rubric")
    ev.add_argument("--force", action="store_true")
    ev.add_argument("--lock-held", help=argparse.SUPPRESS)

    va = sub.add_parser("validate", help="check a run directory against the trace contract")
    va.add_argument("run_dir")

    ui = sub.add_parser("ui", help="serve the local UI on 127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--runs-dir", default="runs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    print(f"foundry {args.command}: not implemented", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
