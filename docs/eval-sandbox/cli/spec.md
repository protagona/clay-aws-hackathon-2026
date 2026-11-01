# The CLIs: tested through their real entry points, documented in one place — Spec

**Track:** Quick   **Date:** 2026-11-01   **Status:** Draft. The user asked for it on 2026-11-01: "test and document the shit out of the CLIs".

## Problem

Developers are meant to use the sandbox from scripts and CI as well as from the UI. Our own specs promise that: the users are "comfortable with a CLI", and aws-deploy I3 is a CLI-style tool. But proof and docs are uneven across the two CLIs, `foundry` and `foundry-remote`.

- **Never driven through their command:** `foundry rubric` and `foundry receive`.
- **Tested in-process only:** `foundry ui` and `foundry tools`.
- **Never tested in `foundry-remote`:** `whoami`, `logout`, the `FOUNDRY_URL`/`FOUNDRY_TOKEN` login, an unreachable server, and a bad URL.
- **Never run against the deployment:** `foundry-remote`'s `run start`, `evaluate`, `batch start`, `compare` and `import`.
- **Never run on Python 3.10:** `foundry-remote`'s "Python 3.10+" claim is only checked by parsing the code as 3.10.
- **Help text:** about 40 options show no `--help` text.
- **Docs:** the CLI is described across the README, `docs/deploy.md` and docstrings. No page lists every command, option, default, output and exit code, or shows how to script them.

## Acceptance criteria

- **C1. Help text.** Every command, subcommand, option and positional of both CLIs shows help text in `--help`. A test walks both parsers and fails on any argument without help.
- **C2. One reference.** `docs/cli.md` documents both CLIs. For every command it gives:
  - what the command does, with its options and their defaults
  - what it prints on stdout and on stderr
  - its exit codes
  - the files it reads or writes
  - the environment variables it reads

  A test fails if a command or option in either parser is missing from `docs/cli.md`. The README and `docs/deploy.md` link to it rather than repeating it.
- **C3. Scripting recipes.** `docs/cli.md` has a "Use it in your workflow" section. Each of these recipes is run for real and its output recorded:
  - (a) gate CI on a run's score using `--json` and exit codes, locally
  - (b) the same against a deployment, with `FOUNDRY_URL`/`FOUNDRY_TOKEN`
  - (c) compare two models with a batch
  - (d) score an agent we didn't build with `external`
  - (e) re-score saved runs against a new rubric
- **C4. Every `foundry` command through its real entry point.** Each command is tested in a fresh process (`python -m foundry`), faking only the network boundary. Every exit code the reference documents is covered. New tests cover:
  - `rubric list`, `activate` (draft and history), `discard`, and `draft` with a judge that can't draft (exit 2)
  - `receive`: it starts, turns one posted trace into a run, and exits 0 on SIGINT; a taken port gives exit 2
  - `ui`: it serves `/healthz` from a fresh process
  - `tools`: in a project whose only config uses the in-repo STAC server, it prints that server's tools as JSON and exits 0. In a project with no configs it exits 1, and with `--out` it keeps the previous file
- **C5. Every `foundry-remote` command, in a fresh process, against a real local server.** The `.pyz` the server builds is run as its own process. Tested:
  - every command in text and `--json` mode
  - `--json` given either before or after the subcommand
  - `whoami` and `logout`
  - the `FOUNDRY_URL`/`FOUNDRY_TOKEN` login overriding the saved one
  - exit 2: no login, a non-https URL, a missing import file
  - exit 1: an unreachable server, an unknown run, a waited-for run that fails
  - exit 130 on Ctrl-C while waiting
- **C6. Python versions.** The `.pyz` passes C5's command sequence on real CPython 3.10 and on the project's 3.12. *(Widened during Build: it also passes on macOS's built-in Python 3.9, so the docs now say 3.9+.)*
- **C7. Live: the deployment.** Every `foundry-remote` command is run against the deployed URL with a temporary token: offline runs, and one Bedrock run and scoring. The token is revoked and the test runs and batch are deleted afterwards. The log is in `docs/eval-sandbox/cli/evidence/`.
- **C8. Live: Bedrock on a laptop.** These are run against real Bedrock and recorded:
  - `foundry run --evaluate`
  - `foundry batch --models` with 2 models and 1 prompt
  - `foundry rubric draft --text`
  - `foundry external` with `examples/external_strands_agent.py`

## Non-goals

- New commands, or changing a command's output or exit codes. The exception is a bug this work finds; it gets fixed and recorded.
- A man page, shell completion or a PyPI release of `foundry-remote`.
- A timeout option for `--wait`. It is documented: the deployment marks a lost job interrupted within 32 minutes, so wrap `--wait` in your CI's own timeout.

## Assumptions and open questions

- **Live cost:** under $1 in total (about $0.15 per Bedrock run plus scoring). The cloud part counts against the deployment's $30 monthly cap, which has room.
- **Python 3.10:** uv installs CPython 3.10 from python-build-standalone, the interpreters uv itself ships with ([uv docs: Python versions](https://docs.astral.sh/uv/concepts/python-versions/)). If that isn't available, C6 is recorded as unmet rather than replaced with something else.
- **`--json` after the subcommand:** today argparse only accepts it before the subcommand. Accepting it in both places is additive, so it's in scope (C5).

## Plan

1. **Help text (C1):** add help to every argument in `cli.py`, `cli_rubric.py` and `cli_remote.py`, and fix `cli.py`'s docstring, which is the parser's description and lists only 8 of the 11 commands. `--json` is also accepted after the subcommand. Add the parser-walk test.
2. **`foundry` tests (C4):** a new `tests/test_cli_commands.py` covers rubric, receive, ui and tools in fresh processes, using `FOUNDRY_HOME` set to a copy of the project. Read each command's code for its exit paths and test each one.
3. **`foundry-remote` tests (C5):** extend `tests/test_cli_remote.py` with one test per command group and error path, still running the real `.pyz` against a real local server.
4. **Python 3.10 (C6):** a pytest that runs the `.pyz` under `uv python find 3.10` if one is installed (skipped otherwise), plus a recorded run with 3.10 installed.
5. **Reference and recipes (C2, C3):** write `docs/cli.md`, add the drift test that compares it with the parsers, link it from the README and `docs/deploy.md`, then run each recipe and record the output.
6. **Live (C7, C8):** create a temporary token, run every remote command, then revoke the token and delete the test runs. Run the Bedrock commands on the laptop. Record both under `evidence/`.
7. **Verify:** the full suite, ruff and gitleaks; a subagent review of the docs and tests that sees only the spec and the diff; the handoff.
