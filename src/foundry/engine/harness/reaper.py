"""Run an MCP server and make sure it dies with the harness.

`python -m foundry.engine.harness.reaper -- <command> [args...]`

The server inherits this process's stdin/stdout/stderr (the MCP stdio pipe) and runs in its own process group.
If the harness disappears (even SIGKILL, which leaves no chance to clean up), this process notices that its
parent changed and kills the server's whole group. macOS has no parent-death signal, so it polls.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

POLL_S = 0.5
GRACE_S = 3.0


def _kill_group(proc: subprocess.Popen) -> None:
    for sig, wait in ((signal.SIGTERM, GRACE_S), (signal.SIGKILL, 1.0)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "--" or len(argv) < 2:
        print("usage: python -m foundry.engine.harness.reaper -- <command> [args...]", file=sys.stderr)
        return 2
    parent = os.getppid()
    proc = subprocess.Popen(argv[1:], start_new_session=True)  # own process group; inherits stdio

    def forward(signum, _frame):
        _kill_group(proc)
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)
    while True:
        code = proc.poll()
        if code is not None:
            return code
        if os.getppid() != parent:  # the harness is gone
            _kill_group(proc)
            return 0
        time.sleep(POLL_S)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
