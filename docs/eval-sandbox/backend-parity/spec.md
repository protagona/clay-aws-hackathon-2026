# One code path for the laptop and AWS — Spec

**Track:** Standard   **Product stage:** Introduction
**Status:** Built   **Last updated:** 2026-11-01

## Problem

Anyone who changes what the app does has to remember a second, cloud-only code path, and a miss passes every laptop test and breaks only in AWS. In practice, a change means:

- **Reads and writes:** about 40 cloud sync calls (`cloud_state.pull_*`, `project_write`, `project_version`, `on(ws)`) are spread across 7 service modules. They do nothing on a laptop.
- **Board cards:** they're built two ways. The laptop computes them on read; the cloud stores them when a run is indexed, then assembles the board with different KPI, sorting and "interrupted" logic.
- **Batches:** they run on two orchestrators. The laptop uses `engine/batch.py`; the cloud uses `workers.batch_prepare`, `batch_item` and `batch_finalize`. They differ on resume, re-scoring, `summary.md`, `logs/` and `--eval-config`.
- **Job kinds:** they're string literals handled in about 10 separate `if kind == …` chains across `jobs.py`, `cloud_jobs.py` and `workers.py`.
- **Tests:** no API test runs against the cloud setup. The 54 laptop API tests never touch the cloud backend.

At the hackathon this costs teammates time on every feature, and costs the team demo failures that show up only after a deploy.

## Users

- **Primary:** hackathon contributors who change services, the API or the engine, and may never deploy to AWS themselves.
- **Secondary:** the operator who deploys, and whoever reviews contributors' changes.

## Goals

- A feature written once works on both setups, and a missed step fails a test on the laptop, before any deploy.
- Code in `services/` doesn't know which setup it runs in.
- Batch behaviour, board cards and job kinds each have one definition.

## Non-goals

- **Changing what users see** in the API, UI or CLI. The exceptions are the behaviour changes listed under AC9; each is intended.
- **New features**, for example `foundry-remote pull` or a `max_seconds` cap. Those are follow-ups.
- **Changing the cloud infrastructure:** stacks, IAM, the Step Functions shape. The state machine keeps calling Prepare, Map items and Finalize.
- **Merging the laptop and cloud storage.** Each keeps its own store. Moving data between them stays out of scope.
- **Re-architecting the engine** beyond the batch runner.

## User stories

- As a contributor, I add a page or route that reads runs or prompts, and it works in AWS without my knowing how the cloud stores them.
- As a contributor, I add a field to a board card in one function, and both boards show it, with no re-indexing step or deploy order to remember.
- As a contributor, I change how a batch item runs, and both the laptop and the cloud batch pick it up.
- As a contributor, I add a background job kind with one registry entry and a `foundry` subcommand.
- As a reviewer, CI tells me when a change breaks the cloud setup.

## Acceptance criteria

- [ ] **AC1. Both setups run the same API tests.**
  - The API test suites (`test_api.py`, `test_api_contract.py`, and the `test_api_ports.py` tests that go through HTTP) run against a laptop app and a cloud app (fake store, real mirror and cloud job backend, jobs executed by the real worker code).
  - Each test either runs on both setups, or carries a marker naming it laptop-only or cloud-only, with a one-line reason.
  - At most 10 tests are single-setup.
- [ ] **AC2. No cloud code in services.**
  - Outside the backend modules (the cloud state implementation, `cloud_jobs.py`, `workers.py`), no module in `services/` or `api/` imports `cloud_state`, `cloud_jobs`, `workers`, `data.store`, `data.mirror` or `data.index`, or reads `ws.cloud`.
  - `tests/test_architecture.py` enforces this, including function-local imports, and has a negative self-test.
- [ ] **AC3. One state interface.** Services read and write project, run, batch, tool-cache and upload state only through one interface on the workspace (`ws.state`), which has a laptop implementation and a cloud implementation. A new read that skips the interface on the cloud setup fails at least one AC1 test.
- [ ] **AC4. One board path.**
  - **One builder:** board cards, KPIs, sorting, and the "evaluating" and "interrupted" overlays come from one assembly function, fed with the same per-run rows on both setups.
  - **The same board everywhere:** for the same runs and job states, `GET /api/runs` returns the same JSON on both setups, apart from timestamps. An AC1 test checks this.
  - **Tolerant of stored cards:** a card field added later is shown, or is absent when unknown, without a 500 when stored cards are older or newer than the code.
- [ ] **AC5. Re-indexing without a hand-written script.** An operator can rebuild the cloud's stored cards with one command, `scripts/cloud.sh reindex`. It's idempotent and reports how many runs it updated.
- [ ] **AC6. One batch orchestrator.**
  - The batch's planning, each item (run, skip on budget, resume or retry naming, score unless already scored, summary row) and finalizing (`batch.json`, `summary.csv`, `summary.md`, exit code) live in one engine module.
  - Both `foundry batch` and the cloud's Prepare, item and Finalize call that module.
  - For the same inputs, the batch's `batch.json` fields, run ids, row statuses, `summary.csv` and `exit_code` match on both setups, apart from timestamps, durations and spend sources. An AC1 test checks this.
- [ ] **AC7. One job-kind registry.**
  - Every job kind (run, evaluate, batch, tools; reservation-only draft) is declared once, with its attributes: singleton, shown when it fails, deadline, busy message, worker handling and post-run hooks.
  - The laptop backend, the cloud backend and the worker read the registry instead of `if kind == …` chains.
  - A test fails if any kind string used to start, reserve or dispatch a job isn't registered.
- [ ] **AC8. Documented.**
  - `docs/architecture.md` has a section, "Writing a feature once", that covers adding a read, a write, a card field, a job kind and a batch change, and the new architecture rule.
  - `docs/deploy.md` has the laptop vs AWS difference table and the `reindex` command.
- [ ] **AC9. Behaviour changes are intended and listed.**
  - **Laptop:** a run that the UI started and whose `foundry run` process exited while `run.json` still says running shows as `interrupted`, as on the cloud. Runs started outside the UI (the CLI, a batch, the receiver) or before a server restart are unchanged.
  - **Cloud batches:** they write `summary.md`, honour `--eval-config`, and skip scoring a run that's already scored.
  - **Resume stays laptop-only:** re-running a `foundry batch` with the same `--out`. A cloud batch is new each time, and its items stay idempotent.
  - Nothing else changes: the full suite still passes.
- [ ] **AC10. Live.**
  - **Deployed:** the change is deployed to the sandbox with the existing smoke checks passing.
  - **Exercised:** a run, a scoring, an offline batch and a prompt edit work through the deployed URL.
  - **Re-indexed:** `cloud.sh reindex` was run on the real data, and the board still shows 149 runs.

## Success signals

- **During the hackathon:** zero changes that pass the laptop suite but break the cloud after a deploy.
- **Contributor effort:** a contributor who adds a route or job kind touches no cloud module.

## Constraints

- **Revertible:** reverting the commits restores today's behaviour, and the data format stays compatible both ways. The stored card and summary stay readable by the old code, and the old index is readable by the new code.
- **Board speed:** the cloud board must not pull every run. It keeps reading the index, so cold-load time doesn't get worse than today's 2.4 s.
- **Tests:** the full suite must stay under about 5 minutes locally (today, 3 minutes).
- **Layers:** the layer rules in `tests/test_architecture.py` keep holding.

## Assumptions and open questions

- **Assumption:** the cloud app in tests can execute jobs with the real worker code and real `foundry` subprocesses, using the fault-server configs, so no network is needed. *If wrong:* jobs on the cloud app use the existing `fake_runner`, and AC1 states that this runner is the one difference.
- **Assumption:** Step Functions keeps passing the same event shapes to the worker (`batch_prepare`, `batch_item`, `batch_finalize`), so no infrastructure change is needed. *If wrong:* the design names the event change, and Launch deploys it with the code.
- **Open:** should the laptop also keep a stored index for the board, rather than computing cards on read? *Owner:* me, in Design. *Default:* compute on read on the laptop, and keep the shared assembly function.
- **Open:** where does the cloud state implementation live, given that services must not import cloud modules? *Owner:* me, in Design. *Default:* a backend module that only `server.py` and the entry points wire in, listed in the architecture rule as a backend.
