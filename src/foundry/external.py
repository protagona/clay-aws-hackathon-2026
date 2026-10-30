"""`foundry external`: run any agent command with its OpenTelemetry export pointed at a private receiver, then turn
what it sent into run directories, and optionally evaluate them (increment-4 X1-X3).

The command runs as an argv list (never through a shell), as the user, with the user's environment plus the OTLP
endpoint and the prompt. It is not sandboxed: it is whatever the user typed.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

EXTRA_WAIT_S = 10.0  # after the command exits, how long to wait for runs still being written


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    proc.wait()


def main(args) -> int:
    from foundry.cli import _print_scores
    from foundry.config import ConfigError, load_eval_config
    from foundry.evaluate.evaluate import EvaluateError, evaluate
    from foundry.receiver import GRACE_S, Receiver
    from foundry.runstore import RunDir
    from foundry.tasks import UnknownPrompt, resolve_prompt

    cmd = list(args.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("foundry external: give the agent command after --, e.g. -- python my_agent.py", file=sys.stderr)
        return 2
    try:
        prompt = resolve_prompt(args.task, args.prompt)
        cfg = load_eval_config(args.config)
    except (ConfigError, UnknownPrompt) as exc:
        print(exc, file=sys.stderr)
        return 2

    rec = Receiver(Path(args.runs_dir), args.task, prompt.prompt_id, cfg.prices, port=0,
                   log=lambda msg: print(msg, file=sys.stderr, flush=True)).start()
    endpoint = f"http://127.0.0.1:{rec.port}"
    otel = {"OTEL_EXPORTER_OTLP_ENDPOINT": endpoint, "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": f"{endpoint}/v1/traces",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf"}
    env = {**os.environ, **otel, "FOUNDRY_TASK_ID": args.task, "FOUNDRY_PROMPT_ID": prompt.prompt_id,
           "FOUNDRY_PROMPT": prompt.prompt}
    print(f"receiving on {endpoint}; running: {' '.join(cmd)}", file=sys.stderr, flush=True)
    try:
        proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=sys.stderr.fileno(),
                                stderr=sys.stderr.fileno(), start_new_session=True)
    except OSError as exc:
        rec.close()
        print(f"Cannot start {cmd[0]!r}: {exc.strerror or exc}", file=sys.stderr)
        return 2

    timed_out = False
    try:
        rc = proc.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        timed_out, rc = True, None
        _kill(proc)
        print(f"Command still running after {args.timeout:g}s: killed", file=sys.stderr)
    except KeyboardInterrupt:
        _kill(proc)
        rec.close()
        raise
    # The exporter's HTTP requests complete before the command exits, so every span is already accepted;
    # only the delayed run writes (GRACE_S after each agent span) can still be pending.
    rec.wait_idle(GRACE_S + EXTRA_WAIT_S)
    rec.close()

    if not rec.written:
        print("No agent runs received. The command must export OpenTelemetry GenAI spans (an invoke_agent span per "
              "run) over OTLP/HTTP; it was given " + " ".join(f"{k}={v}" for k, v in otel.items())
              + (f" and exited {rc}." if rc is not None else "."), file=sys.stderr)
        return 2

    status_ok, eval_failed = True, False
    for run_dir in rec.written:
        print(run_dir, flush=True)
        status_ok &= RunDir(run_dir).read_run().status == "succeeded"
        if args.evaluate:
            try:
                _print_scores(run_dir, evaluate(run_dir, cfg))
            except (EvaluateError, PermissionError) as exc:
                print(f"Evaluation failed for {run_dir}: {exc}", file=sys.stderr)
                eval_failed = True
    if eval_failed:
        return 2
    if timed_out or rc != 0:
        print(f"Command {'timed out' if timed_out else f'exited {rc}'}", file=sys.stderr)
        return 1
    return 0 if status_ok else 1
