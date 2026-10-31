"""Command-line entry point: foundry run | evaluate | validate | ui | batch | import | receive | external."""

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
    run.add_argument("--evaluate", action="store_true",
                     help="then summarize and score the run with the same config (one command: run, trace, report)")

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
    ba.add_argument("--runs-dir", help="where run dirs go (default <out>/runs); the UI uses its own runs dir")
    ba.add_argument("--run-prefix", help="prefix for run ids, so batches can share a runs dir")

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

    ex = sub.add_parser("external", help="run any OpenTelemetry-instrumented agent command and record its runs",
                        usage="foundry external --task T --prompt P [options] -- <command> [args...]")
    ex.add_argument("--task", required=True)
    ex.add_argument("--prompt", required=True, help="prompt id; its text is passed to the command as FOUNDRY_PROMPT")
    ex.add_argument("--config", default="config/offline.toml", help="evaluation config; its [prices] give cost_usd")
    ex.add_argument("--runs-dir", default="runs")
    ex.add_argument("--timeout", type=float, default=600, help="kill the command after this many seconds")
    ex.add_argument("--evaluate", action="store_true", help="also evaluate each received run with --config")
    ex.add_argument("cmd", nargs=argparse.REMAINDER, help="the agent command, after --")

    va = sub.add_parser("validate", help="check a run directory against the trace contract")
    va.add_argument("run_dir")

    from foundry.cli_rubric import add_parser as add_rubric_parser

    add_rubric_parser(sub)

    tl = sub.add_parser("tools", help="list every tool of every MCP server the configs use, with descriptions (JSON)")
    tl.add_argument("--out", help="write the JSON here (atomically) instead of printing it")

    ui = sub.add_parser("ui", help="serve the local UI on 127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--runs-dir", default="runs")
    return parser


def _cmd_tools(args: argparse.Namespace) -> int:
    import json
    import os
    import tempfile

    from foundry.config import project_root
    from foundry.engine.harness.tools import list_tools

    import signal

    def give_up(*_):  # a server that hangs while listing must not leave the UI "refreshing" forever
        raise SystemExit("foundry tools: gave up after 10 minutes")

    signal.signal(signal.SIGALRM, give_up)
    signal.alarm(600)
    configs = sorted((project_root() / "config").glob("*.toml"))
    with tempfile.TemporaryDirectory() as logs:
        result = list_tools(configs, Path(logs))
    text = json.dumps(result, indent=1)
    listed = bool(result["servers"]) and any(not s["error"] for s in result["servers"])
    if args.out and not listed:  # keep the last good list: an all-failed listing must not replace it (gate pass 3)
        print("Couldn't list tools from any server; kept the previous list.", file=sys.stderr)
        return 1
    if args.out:
        out = Path(args.out)
        tmp = out.with_name(f"{out.name}.tmp-{os.getpid()}")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, out)
    else:
        print(text)
    failed = [s["name"] for s in result["servers"] if s["error"]]
    if failed:
        print(f"Couldn't list tools from: {', '.join(failed)}", file=sys.stderr)
    return 0 if result["servers"] and len(failed) < len(result["servers"]) else 1


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
    from foundry.engine.harness.runner import run
    from foundry.data.runs import is_valid_run_id
    from foundry.data.tasks import UnknownPrompt, resolve_prompt

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
    print(run_dir, flush=True)
    if status != "succeeded":
        print(f"Run {status}: see {run_dir / 'trace.jsonl'}", file=sys.stderr)
    if args.evaluate:  # failed runs are scored too: the trace says where the agent struggled
        from foundry.engine.evaluate.evaluate import EvaluateError, evaluate
        from foundry.engine.evaluate.report import print_scores

        try:
            print_scores(run_dir, evaluate(run_dir, cfg))
        except (EvaluateError, PermissionError) as exc:
            print(f"Evaluation failed: {exc}", file=sys.stderr)
            return 2
    return 0 if status == "succeeded" else 1


def _cmd_evaluate(args: argparse.Namespace) -> int:
    from foundry.config import ConfigError, load_eval_config
    from foundry.engine.evaluate.report import print_scores
    from foundry.engine.evaluate.evaluate import EvaluateError, evaluate

    try:
        cfg = load_eval_config(args.config)
        scores = evaluate(args.run_dir, cfg, rubric_file=args.rubric, force=args.force, lock_token=args.lock_held)
    except (ConfigError, EvaluateError) as exc:
        print(exc, file=sys.stderr)
        return 2
    except PermissionError as exc:
        print(f"Cannot write evaluation files: {exc}. Check permissions on {args.run_dir}.", file=sys.stderr)
        return 2
    print_scores(args.run_dir, scores)
    return 0


def _cmd_ui(args: argparse.Namespace) -> int:
    import uvicorn

    from foundry.config import project_root
    from foundry.server import create_app

    app = create_app(args.runs_dir, project_root(), port=args.port)
    print(f"Foundry UI on http://127.0.0.1:{args.port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def _cmd_batch(args: argparse.Namespace) -> int:
    from foundry.engine.batch import main as batch_main

    return batch_main(args)


def _cmd_import(args: argparse.Namespace) -> int:
    from foundry.config import ConfigError, load_eval_config
    from foundry.engine.importer import ImportError_, import_spans, spans_from_file
    from foundry.data.runs import is_valid_run_id

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


def _cmd_external(args: argparse.Namespace) -> int:
    from foundry.engine.external import main as external_main

    return external_main(args)


def _cmd_receive(args: argparse.Namespace) -> int:
    from foundry.engine.receiver import main as receive_main

    return receive_main(args)


def _cmd_rubric(args: argparse.Namespace) -> int:
    from foundry.cli_rubric import main as rubric_main

    return rubric_main(args)


COMMANDS = {
    "rubric": _cmd_rubric,
    "import": _cmd_import,
    "receive": _cmd_receive,
    "external": _cmd_external,
    "batch": _cmd_batch,
    "ui": _cmd_ui,
    "run": _cmd_run,
    "evaluate": _cmd_evaluate,
    "validate": _cmd_validate,
    "tools": _cmd_tools,
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
