# One code path for the laptop and AWS — Handoff

**Spec:** ./spec.md   **Design:** ./design.md   **Date:** 2026-11-01   **Track:** Standard   **Build:** `14e8c38`, live in the sandbox (later commits change only tests and docs)

**What shipped:** a feature is now written once for both setups. Services reach state through `state.of(ws)` and build board cards with one builder. Job kinds are declared once, and batches run on one engine orchestrator. Every API test runs on the laptop app and on the cloud app, where each request starts from an empty copy of the data, so a missed cloud step fails CI instead of a deploy.

## Verification

- [x] **AC1, the same API tests on both setups:** `tests/backends.py` and the `backend` fixture in `tests/conftest.py`.
  - `test_api.py`, `test_api_contract.py` and `test_api_ports.py` run on both setups: 153 tests in 48 s.
  - Four tests are laptop-only, each with its reason (symlinks, which the cloud's store can't hold; the receiver). `test_single_setup_tests_are_few` caps them at 10.
  - **Evidence that the net catches a miss** (`evidence/mutations.txt`): every `fresh_*` / `record_run` call in services was removed one at a time.
    - Each removal fails a cloud test, except three that are redundant: in the same request, `configs.task_ids` already fetches the projects.
    - Verify pass 1 found five calls no test reached. Each now has a test: the prompt text lock and run counts, the tool menu, the rubric versions' run counts, and re-importing a trace.
- [x] **AC2, no cloud code in services:** `test_only_backend_modules_know_the_cloud` passes on the real code. `test_the_backend_check_catches_a_leak` shows each of its three checks reports: a module import, a function-local import of a name, and `.cloud`.
- [x] **AC3, one state interface:**
  - **The seam:** `services/state.py` (`LocalState`, `of`, `expecting`) and `CloudState`. Every former `cloud_state.*` call, `on(ws)` branch and `ws.cloud` read in services and api goes through the state.
  - **The cold copy:** the harness's empty-copy client makes a skipped fetch visible, as AC1's evidence shows.
- [x] **AC4, one board:**
  - **One builder and assembler:** `services/cards.py` builds every card, and `runs.board` assembles the board for both setups.
  - **Same board:** `test_the_same_runs_make_the_same_board_on_both_setups` gives identical `/api/runs` JSON (minus time-of-day fields) for clean, recovered, failed, hostile and unreadable runs.
  - **Tolerates stored cards:** in `test_a_stored_card_from_other_code_never_breaks_the_board`, an extra key and a missing key in a stored card don't cause a 500.
- [x] **AC5, re-indexing:**
  - **Tests:**
    - `test_reindex_rewrites_only_stale_cards_and_is_idempotent`: one card is updated, the second run updates none, and no version changes.
    - `test_reindex_skips_live_runs_and_counts_a_run_that_moved`: a running job and a batch item are skipped, and a moved run counts as busy.
    - `test_reindex_pages_by_its_time_budget`.
  - **Live** (`evidence/reindex.txt`): 151 runs, 0 updated, in 38 s cold and 2 s warm.
- [x] **AC6, one batch orchestrator:**
  - **The parity test:** `test_the_same_batch_gives_the_same_results_on_both_setups` runs one batch through the API on each setup and compares:
    - `batch.json`, including `configs`
    - the typed summary rows
    - the run ids
    - the scored cards
    - the exit code
    - that `summary.md` exists
  - **The Lambda working directory:** `test_a_models_batch_prepares_from_any_working_directory` covers it. Without the path fix it fails with `FileNotFoundError`.
  - **No regressions:** `test_batch.py` and `test_cloud_batches.py` are unchanged and green.
- [x] **AC7, one job-kind registry:** `services/job_kinds.py`.
  - `test_every_job_kind_used_is_declared_once` finds every kind literal in services and api by AST, and checks each `start` against the registry.
  - `test_an_unknown_job_kind_is_refused_and_an_old_record_is_tolerated`.
  - `test_worker_events_are_listed_once`: worker events are a separate list.
- [x] **AC8, documented:**
  - `docs/architecture.md`: "Writing a feature once", the backend rule, and the new import order.
  - `docs/deploy.md`: the laptop vs AWS table, and the `reindex` command and runbook.
  - The CHANGELOG.
- [x] **AC9, intended behaviour changes:**
  - Laptop "interrupted": `test_a_laptop_run_whose_process_died_shows_interrupted`.
  - Cloud batch `summary.md` and `--eval-config`, and not re-scoring a scored run: covered by the batch parity test and the engine's `run_item`.
- [x] **AC10, live:**
  - **Deployed:** `9fd8422`, after `97f21c6`. Both deploys' health checks passed (`evidence/deploy.log`, `deploy-2.log`).
  - **Exercised through the deployed URL** with a temporary token (`evidence/live-ac10.txt`):
    - **A run, its scoring and an offline batch:** scored cards.
    - **A prompt edit:** the change was read back, a write sent with the old version got 409, and the original was restored exactly.
    - **After the fix:** one more batch recorded `configs: ["config/offline.toml"]`.
  - **Re-indexed:** the board showed 151 runs (149 plus this check's).
  - **Cleanup:** the token is revoked. The check's records still need deleting; see Follow-ups.

**Suites:**
- pytest: 569 passed, 11 skipped (the laptop-only tests on the cloud setup, and the 7 cloud-only stale-version cases on the laptop), 4 network tests deselected, in 3 m 29 s. 5 single-setup tests, against the cap of 10.
- The parity tests passed three times in a row.
- ruff and `tsc` are clean, and gitleaks found no leaks.

**Exercised live:** two deploys to the sandbox, the live check above, and `cloud.sh reindex` on the real data.

**Gate history:**
- **Define:** self-review.
- **Design:** three subagent passes.
  - Pass 1 found 8 blockers: an import cycle, the laptop interrupted rule, CSV-typed rows, cloud resume, re-indexing safety and timeouts, the registry vs worker events, and AC3 enforcement.
  - Pass 2 found 4: the cold hook deleting the project root, re-index versions, batch items not skipped, and the missing push after scoring.
  - Pass 3 found 2: project files in the harness, and config paths in a Lambda. You approved the resolutions on 2026-11-01.
- **Plan:** self-review.
- **Verify:** two subagent passes.
  - **Pass 1** found 2 blockers:
    1. **AC3 not met:** 5 cloud fetches no test reached. Removing the one in `runs_by_prompt` silently disabled the prompt text lock in AWS. Fixed with tests for each, and the mutation run is recorded.
    2. **A laptop race:** a run finishing during the board read showed as "interrupted". Reproduced, fixed by snapshotting job liveness before reading, with a regression test.
  - **Pass 1's minor findings:**
    - **Fixed:**
      - the architecture rule's blind spots (`getattr`, `importlib`, `__import__`)
      - non-ran summary rows now typed
      - the docs claim about the cold copy, now stating what it covers
      - the polling test's silent exit
      - `batch_list` fetching every run unnecessarily
    - **Noted here:** the rest, under Follow-ups.
  - **Pass 2** confirmed the race fix by reading the code. It found 1 blocker: a project write that skipped `project_write` passed every test, because no cloud test read a write back.
    - **Fixed:** `test_every_prompt_write_is_read_back_by_a_fresh_request` covers create, edit, expected steps, new version and archive, each read back by a cold request. `test_a_write_from_a_stale_page_is_refused` checks every write route's version check.
    - **Write-side mutation run** (`evidence/mutations-writes.txt`): all 16 removals of `project_write` and `state.expecting` are caught.
    - The pass ran out of disk partway, and you freed space.
  - **Pass 3:** no blockers. It checked every write route, re-checked the three redundant reads, and ran its own worker-side mutations. Its two minor findings both predate this change:
    - the cloud worker's publish of a refreshed tool listing has no test
    - a cloud batch item whose run exits 2 after the first push can keep a "running" card

    Both are under Follow-ups.

**Specialists consulted:**
- QA Automation: the harness, mutation checks of the net and of the path fix, and the parity tests.
- Data Engineer: the index shape is unchanged both ways, re-indexing is idempotent and never bumps versions.
- Platform/SRE: re-indexing is paged and bounded, with a client timeout above the worker limit and no retries, and a runbook entry.

**Decisions and deviations:**
- **Found and fixed during Build:**
  - **A warm cloud instance checked runs against stale prompt files** (production since aws-deploy). The cold-instance harness exposed it, and `configs` now fetches the projects first.
  - **Board ties ordered differently** on the two setups. Ties now go by run id; the board parity test found it.
  - **batch.json recorded a server path** in `configs` after the path fix. The live check found it, and `9fd8422` fixed it.
- **Deviation:** `workers.index_run` stays as a thin wrapper over `CloudState.record_run`, because `scripts/cloud_ops.py seed` and older tests call it.
- **Re-indexing updated 0 cards:** the shared builder makes the same cards the cloud stored before, which is correct. The command is ready for the next card field.

**Follow-ups:**
- **Cloud cleanup of this check's records** (5 records: 2 runs, 2 batches and their runs, 30 objects, 6 job records). The auto-mode classifier blocks it for me:
  ```
  ! cd /Users/mpereira/Extra/repos/protagona/clay-aws-hackathon-2026 && PATH="$HOME/.asdf/shims:$PATH" AWS_PROFILE=protagona-sandbox-1 AWS_REGION=us-west-2 uv run python /private/tmp/claude-501/-Users-mpereira-Extra-repos-protagona-clay-aws-hackathon-2026/d98dcd93-7759-46cf-9061-cdc910349cc1/scratchpad/cleanup.py /private/tmp/claude-501/-Users-mpereira-Extra-repos-protagona-clay-aws-hackathon-2026/d98dcd93-7759-46cf-9061-cdc910349cc1/scratchpad/parity/cleanup-manifest.json
  ```
- **Prompt adb-001's project version:** it moved from 35 to 37 (edit, then restore). Its content is unchanged.
- **Other test files** (`test_dashboard.py`, `test_prompts.py`, `test_tools.py`) still build only the laptop app. Converting them follows the same pattern.

- **Minor, from Verify pass 1, accepted:**
  - **Laptop summaries:** they now type their numbers, so an integer score prints as `1.0`, and a missing score as an empty cell instead of `None`.
  - **Laptop `batch.json` configs:** a config passed as an absolute path inside the project is now recorded relative to the project root.
  - **Cloud "evaluating" cards:** their `sub` line is now blank, as on the laptop.
  - **Cloud `batch.json` `runs_dir`:** it now holds the worker's `/tmp` path. The API never exposes it, and laptop batch files always held their own path.
  - **The harness's worker:** its copy isn't emptied between jobs, and it runs batch items one at a time. So a missed pull inside `workers.*`, or a concurrency race between items, isn't tested.
  - **`--eval-config` and "skip an already-scored run":** neither is reachable through the cloud API: the API never sends `--eval-config`, and cloud items always run fresh. Both are tested on the laptop engine path, which the cloud now shares.
  - **Live re-indexing:** it updated 0 cards. A rewrite is shown only by the fake-store tests.
  - **`CloudState`:** it exposes `store` and `mirror` as attributes. The architecture rule doesn't stop a service from reaching them through `state.of(ws)`; convention does.
  - **`runs.interrupted`:** it reads `cards_fresh`, a state attribute, by design.
- **Live after the Verify fixes:** `14e8c38` deployed with its health checks passing. The full live check (AC10) ran on `97f21c6` and `9fd8422`, whose run, scoring, batch and prompt code paths are unchanged by the fixes. (`9fd8422` was amended to `7827571` before pushing, to drop a stray binary, so the code is identical.)

- **From Verify pass 3:**
  - Add a cloud test that runs a `tools` job through the worker and reads the listing back.
  - Record a cloud batch item's run once more when it ends in error, not only when it ran.

**Risks to watch:**
- The cloud parameter roughly doubles the API tests' time (now 48 s for the four files).
- A new route that reads files must call `state.of(ws).fresh_*()`; the cold-copy tests catch it only if a test reaches that route on the cloud setup.
