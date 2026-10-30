"""Command-line entry point: foundry run | evaluate | validate | ui."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


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

    ba = sub.add_parser("batch", help="run prompts x variants x repeats, then summarize")
    ba.add_argument("--config", action="append", required=True, help="a variant config (repeatable)")
    ba.add_argument("--models", help="comma-separated model ids: one variant each, derived from the single --config")
    ba.add_argument("--prompts", required=True, help='comma-separated prompt ids, or "all"')
    ba.add_argument("--repeats", type=int, default=1)
    ba.add_argument("--concurrency", type=int, default=2)
    ba.add_argument("--evaluate", action="store_true", help="also run foundry evaluate on each run")
    ba.add_argument("--eval-config", help="config for evaluation (default: the first variant's config)")
    ba.add_argument("--max-cost", type=float, help="stop launching runs once recorded cost (USD) reaches this")
    ba.add_argument("--out", required=True, help="batch directory (re-run the same command to resume)")

    im = sub.add_parser("import", help="turn OpenTelemetry GenAI traces (OTLP/JSON) from any agent into run dirs")
    im.add_argument("file", help="OTLP/JSON: one ExportTraceServiceRequest, or JSON Lines of them")
    im.add_argument("--task", required=True, help="task id the runs belong to (selects the rubric for evaluate)")
    im.add_argument("--prompt-id", default="imported")
    im.add_argument("--runs-dir", default="runs")
    im.add_argument("--config", help="config whose [prices] are used to compute cost_usd")

    rc = sub.add_parser("receive", help="OTLP/HTTP receiver on 127.0.0.1: incoming agent traces become run dirs")
    rc.add_argument("--task", required=True)
    rc.add_argument("--prompt-id", default="imported")
    rc.add_argument("--runs-dir", default="runs")
    rc.add_argument("--port", type=int, default=4318)
    rc.add_argument("--config", help="config whose [prices] are used to compute cost_usd")

    va = sub.add_parser("validate", help="check a run directory against the trace contract")
    va.add_argument("run_dir")

    ui = sub.add_parser("ui", help="serve the local UI on 127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--runs-dir", default="runs")
    return parser


def _cmd_validate(args: argparse.Namespace) -> int:
    from foundry.contract import validate_run_dir

    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"Not a run directory: {run_dir}", file=sys.stderr)
        return 2
    violations = validate_run_dir(run_dir)
    for v in violations:
        print(v)
    return 1 if any(v.severity == "error" for v in violations) else 0


def _cmd_run(args: argparse.Namespace) -> int:
    from foundry.config import ConfigError, load_config
    from foundry.harness.runner import run
    from foundry.runstore import is_valid_run_id
    from foundry.tasks import UnknownPrompt, resolve_prompt

    try:
        cfg = load_config(args.config)
        prompt = resolve_prompt(cfg.task.id, args.prompt)
    except (ConfigError, UnknownPrompt) as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.run_id is not None and not is_valid_run_id(args.run_id):
        print(f"Invalid --run-id {args.run_id!r}: must match ^[A-Za-z0-9._-]+$", file=sys.stderr)
        return 2
    try:
        run_dir, status = run(cfg, prompt, args.runs_dir, run_id=args.run_id, config_path=args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    except FileExistsError:
        print(f"Run directory already exists for --run-id {args.run_id}", file=sys.stderr)
        return 2
    except PermissionError as exc:
        print(f"Cannot write the run directory: {exc}. Check permissions on --runs-dir {args.runs_dir}.", file=sys.stderr)
        return 2
    print(run_dir)
    if status != "succeeded":
        print(f"Run {status}: see {run_dir / 'trace.jsonl'}", file=sys.stderr)
    return 0 if status == "succeeded" else 1


def _cmd_evaluate(args: argparse.Namespace) -> int:
    from foundry.config import ConfigError, load_eval_config
    from foundry.evaluate.evaluate import EvaluateError, evaluate

    try:
        cfg = load_eval_config(args.config)
        scores = evaluate(args.run_dir, cfg, rubric_file=args.rubric, force=args.force, lock_token=args.lock_held)
    except (ConfigError, EvaluateError) as exc:
        print(exc, file=sys.stderr)
        return 2
    except PermissionError as exc:
        print(f"Cannot write evaluation files: {exc}. Check permissions on {args.run_dir}.", file=sys.stderr)
        return 2
    overall = scores["overall"]
    w, o = scores["workflow"]["score"], scores["output"]["score"]
    fmt = lambda x: "n/a" if x is None else f"{x:.2f}"  # noqa: E731
    print(f"{Path(args.run_dir) / 'report.md'}")
    print(f"overall {fmt(overall) if overall is not None else 'incomplete'} | workflow {fmt(w)} | output {fmt(o)}")
    return 0


def _cmd_ui(args: argparse.Namespace) -> int:
    import uvicorn

    from foundry.config import project_root
    from foundry.ui.app import create_app

    app = create_app(args.runs_dir, project_root(), port=args.port)
    print(f"Foundry UI on http://127.0.0.1:{args.port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def _cmd_batch(args: argparse.Namespace) -> int:
    from foundry.batch import main as batch_main

    return batch_main(args)


def _cmd_import(args: argparse.Namespace) -> int:
    from foundry.config import ConfigError, load_eval_config
    from foundry.importer import ImportError_, import_spans, spans_from_file
    from foundry.runstore import is_valid_run_id

    if not is_valid_run_id(args.prompt_id):
        print(f"Invalid --prompt-id {args.prompt_id!r}: must match ^[A-Za-z0-9._-]+$", file=sys.stderr)
        return 2
    try:
        prices = load_eval_config(args.config).prices if args.config else None
        paths = import_spans(spans_from_file(Path(args.file)), Path(args.runs_dir), args.task, args.prompt_id,
                             str(args.file), prices)
    except (ImportError_, ConfigError) as exc:
        print(exc, file=sys.stderr)
        return 2
    for p in paths:
        print(p)
    return 0


def _cmd_receive(args: argparse.Namespace) -> int:
    from foundry.receiver import main as receive_main

    return receive_main(args)


COMMANDS = {
    "import": _cmd_import,
    "receive": _cmd_receive,
    "batch": _cmd_batch,
    "ui": _cmd_ui,
    "run": _cmd_run,
    "evaluate": _cmd_evaluate,
    "validate": _cmd_validate,
}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    # Strands logs cancelled/failed tool calls with full tracebacks; the trace already records them,
    # so keep the terminal readable.
    import logging

    logging.getLogger("strands").setLevel(logging.CRITICAL)
    handler = COMMANDS.get(args.command)
    if handler is None:
        print(f"foundry {args.command}: not implemented", file=sys.stderr)
        return 2
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
