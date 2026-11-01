# The CLIs: tested and documented — Handoff

**Spec:** ./spec.md   **Date:** 2026-11-01   **Track:** Quick, with a subagent review at Verify   **Build:** `7097990`, live in the sandbox (version 12; the served client refuses an http `FOUNDRY_URL` with exit 2, checked after the deploy)

**What shipped:**
- **One reference:** [docs/cli.md](../../cli.md) covers both command-line tools. For every command it gives:
  - options and defaults
  - stdout and stderr
  - exit codes
  - files and environment variables

  It also has five scripting and CI recipes, each run for real.
- **Help text:** every option shows help in `--help`, and `foundry-remote` takes `--json` before or after the command.
- **Tests:** every command of both tools is tested through its real entry point.
- **Two findings, both fixed:**
  - `foundry receive` dropped a trace it had already acknowledged if Ctrl-C came within 2 s.
  - `foundry-remote` turned out to run on macOS's built-in Python 3.9, so the stated minimum is now 3.9 (it was 3.10).

## Verification

- [x] **C1, help text:** `test_every_command_and_argument_has_help`, for both parsers. It walks every command and argument.
- [x] **C2, one reference:** `test_the_reference_documents_every_command_and_option`, for both tools. It fails if `docs/cli.md` lacks a section for a command, or an option in that section. I checked by removing `--run-prefix` and the `foundry-remote jobs` heading: it failed on each. The README (Quickstart and "Start here") and `docs/deploy.md` link to the reference.
- [x] **C3, recipes:** each was run as written from the repo root; output in `evidence/`.
  1. The CI gate, against Bedrock (`recipe1-ci-gate-local.txt`).
     - The first wording passed (0.96, exit 0), and a 0.99 threshold made it exit 1.
     - After the review it uses `set -euo pipefail` and also fails a failed run. Rerun under `bash -eo pipefail`: a Bedrock run scored 1.0 and the gate exited 0; a run whose tool server crashed scored 0.25 and the gate exited 1.
  2. The deployment gate, with `FOUNDRY_URL`/`FOUNDRY_TOKEN` and no saved login.
     - The first wording ran against the deployment: a Bedrock run scored 1.00, and `jq -e` exited 0 (`c7-remote-live.txt`).
     - The final wording (`${FOUNDRY_URL%/}`, variable checks) ran against a laptop's `foundry ui` with a trailing-slash URL, on Python 3.9 with real Bedrock: exit 0, or exit 1 with a clear message when a variable is missing (`recipe2-ci-gate-remote.txt`).
     - Its first attempt caught a quoting bug in the new wording, now fixed.
  - Every recipe block passes `bash -n`.
  3. A batch comparing two models: 4 runs, $0.86, Kimi K3 at 0.96 and Sonnet 4.6 at 1.00 (`recipe3-batch-compare.txt`).
  4. `external` with the example Strands agent (`recipe4-external-agent.txt`):
     - The first attempt: the agent hit its own 4096-token output limit, and `external` exited 1. That's the documented exit for a failed command, and the run was still recorded and scored at 0.82.
     - The second attempt: exit 0, overall 1.00.
  5. Re-scoring the batch's 4 runs against a rubric file (`recipe5-rescore.txt`). The review asked for a rubric that differs from the active one, so the rerun used `clay-adb-cli-check`, with the criteria accuracy and brevity. All 4 runs recorded it, scored on those 2 criteria, and `evaluate` warned that the rubric id differs from the run's.
- [x] **C4, `foundry` commands in fresh processes:** `tests/test_cli_commands.py`, with `FOUNDRY_HOME` set to a copy of the project and the fault MCP server, so no network is needed.
  - `rubric`: list; activate a draft; roll back to a history file; discard; and the errors (unknown task, unknown draft, offline judge, missing file, no source).
  - `receive`: writes a posted trace and exits 0 on Ctrl-C; a trace that arrives just before Ctrl-C is still written (a regression test); a taken port gives 2.
  - `ui` serves its API from a fresh process.
  - `tools`: lists the fault server's tools. With no server listed it exits 1 and keeps the last `--out` file.
  - Bare `foundry` gives exit 2; `--help` works; `foundry remote` passes the command on.
  - Every exit code `docs/cli.md` lists is now covered in a fresh process. Added after the review:
    - `validate`: exits 0, 1 and 2
    - `receive`: a bad config gives 2, and a trace pending at Ctrl-C prints the "Writing 1 pending trace(s)" message
    - `batch`: 2 for an unknown prompt, 1 when a run can't start
    - `evaluate`: 2 while another scorer holds the run's lock
    - `rubric draft`: 0 for a file that's already a rubric (no model call), 1 for a scripted judge that never replies with a rubric
    - `tools`: now lists the real in-repo STAC server, as C4 says, with its STAC API unreachable
  - `run`, `evaluate`, `import` and `external` were already tested this way (`test_cli.py`, `test_external.py`, `test_importer.py`).
- [x] **C5, `foundry-remote` against a real local server:** `tests/test_cli_remote.py` runs the served `.pyz` as its own process, with no network needed.
  - Every command in text and `--json` mode, with `--json` before and after the command (read-only commands give the same JSON both ways).
  - `whoami` and `logout`, including logging out with nothing saved, and a second login tightening a looser file mode to 0600.
  - `FOUNDRY_URL`/`FOUNDRY_TOKEN` overriding the saved login. An `http://` URL that isn't this machine exits 2 and sends nothing (a review finding: the variable used to skip `login`'s check).
  - `--json` after a command group (`run --json show`).
  - Exit 2: no args, a missing option, a bad `--limit`, a non-https URL, a missing import file, not logged in.
  - Exit 1: an unreachable server (and nothing saved), a failed waited-for run (whose text still prints), an unknown prompt, unknown runs and batches, a bad trace file, a second run while one is in progress.
  - Exit 130 on Ctrl-C while waiting.
- [x] **C6, Python versions:** `test_the_pyz_on_older_pythons` runs the whole sequence on macOS's Python 3.9.6 and on CPython 3.10.22 (installed with `uv python install 3.10`). The rest of the suite uses 3.12. The parse check is now for 3.9.
- [x] **C7, live: the deployment:**
  - Every `foundry-remote` command ran against the deployed URL, on Python 3.9.6, with a temporary token: 35 commands, every exit as documented (`c7-remote-live.txt`).
  - The token was revoked; only the operator's token remains.
  - Its records were deleted afterwards (7 records: 6 runs and a batch, 49 objects, 10 job records). The operator ran `cleanup.py` with `cleanup-manifest.json` after the auto-mode classifier blocked me from running it. The deployment is back to 149 runs, and the batch record is gone. S3 versions and point-in-time recovery keep them restorable for 35 days.
  - The list is exactly this check's records. The "created" line in the log also names two earlier batches that `batches` printed, but they're not in `cleanup-manifest.json`, which I checked.
- [x] **C8, live: Bedrock on a laptop:**
  - `run --evaluate` (recipe 1)
  - `batch --models` (recipe 3)
  - `external` (recipe 4)
  - `rubric draft --text`: a valid draft with two interpretation notes in 9 s, then discarded (`c8-rubric-draft-live.txt`)

**Suites:**
- pytest: 478 passed, 4 network tests deselected.
- The CLI test files passed 3 runs in a row after the review fixes (28 of 28 each time).
- ruff is clean, and gitleaks found no leaks.

**Exercised live:**
- **Cloud:** `e9844a2` is deployed (version 11), and every remote command ran against it.
- **Laptop:** Bedrock checks cost about $1.40 in total:
  - recipe 1: one run and its scoring
  - recipe 3: four runs and their scoring
  - recipe 4: two runs and their scoring
  - recipe 5: four re-scorings
  - one rubric draft
- **Cloud spend:** one Bedrock run and its scoring, about $0.13. The offline runs cost nothing.

**Gate history:**
- **Define:** self-review. Every criterion can be checked, non-goals are explicit, and the open questions have defaults.
- **Verify:** one subagent pass, which saw only the spec, the diff, `docs/cli.md` and the evidence. It found 4 blocking issues; all are resolved:
  1. C4's "every documented exit code" wasn't all covered. Tests were added for each.
  2. C7's cleanup wasn't done, and the "created" line over-counted. C7 now says the cleanup is pending, and the cleanup list was checked: it covers only this check's records.
  3. Recipe 5 used a copy of the active rubric, so it proved nothing. It was rerun with a distinct rubric.
  4. `FOUNDRY_URL` skipped the https check. The check is now shared with `login`, and tested.
- **Minor findings from that pass:**
  - Fixed:
    - recipe 1 under `bash -e`, and a failed run passing the gate
    - placeholders that become shell redirections when pasted
    - recipe 2's trailing slash
    - the stdout convention's wording
    - `--json` after `run` and `batch`
    - the `AWS_REGION` row
    - `deploy.md` repeating the exit codes
    - the `tools` timeout exit
    - the batch-busy message
    - the receiver's silent wait, and the traceback on a second Ctrl-C
    - the 3.9 test not parsing its JSON
    - the `ui` test's unclear failure, now on `/healthz`
    - the `tools` test, now on the STAC server
    - a weak assertion
    - help text for the subcommand groups
    - the batch line's decimals
    - error-message guidance in the troubleshooting table
  - Deferred: see Follow-ups.

**Specialists consulted:**
- QA Automation: every criterion maps to a test that runs in CI, through real entry points, faking only the network.
- Exploratory Tester: Ctrl-C mid-wait and mid-receive, a re-login over a loose file mode, two runs at once, a batch read before its record exists, and the macOS system Python.
- API Designer / DX: `--json` placement, and errors that say what to do.
- Technical Writer: one reference, linked from the README and the runbook.

**Decisions and deviations:**
- **Fixed during Build (spec non-goal exception, "a bug this work finds"):** the receiver now waits up to 32 s for pending traces on Ctrl-C.
- **C6 widened:** the client's minimum is now Python 3.9 rather than 3.10, because it was tested there.
- **Additive change:** `--json` is also accepted after the command (spec Assumptions).
- **Recipes trimmed so they could be run as written at low cost:** recipe 3 uses one prompt (8 → 4 runs), and recipe 5 re-scores a batch instead of every local run.

**Follow-ups:**
- **Deferred from the review:**
  - The docs test checks only one way: parser → doc. An option the doc names but the parser lacks isn't caught, and defaults aren't compared.
  - `Unknown draft` / `Unknown rubric version` don't name the id. The troubleshooting table covers it.
  - `foundry receive --task` isn't checked against `tasks/`. It's documented.
  - `foundry external` on Ctrl-C ends with a traceback.
  - The reference's synopsis blocks use `<placeholders>`, so they aren't runnable as is. The recipes are.
- **Local:** the recipes left three real Bedrock runs in `runs/` (cited by the evidence) and `batches/kimi-vs-sonnet/`. Both are git-ignored, and you can delete them.
- **`--wait` has no timeout of its own** (a non-goal). It's documented: use your CI step's timeout.
- **`batch show` says "Batch not found"** for a moment after `batch start`. It's documented, and `--wait` handles it.
- **Cosmetic:** `foundry-remote batches` shows `offline.toml` in the MODELS column for a config's default model.
- **Not reached live:** the "already being scored" refusal, because the offline judge finishes in 0.7 s. It's covered by unit tests.

**Risks to watch:**
- `test_the_reference_documents_every_command_and_option` makes adding an option fail CI until `docs/cli.md` documents it. That's intended.
