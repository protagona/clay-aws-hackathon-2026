# Changelog

## Unreleased (since 0.1.0, `prototype` branch, through 2026-11-01)

Each increment's spec, design and verification record is under [docs/eval-sandbox/](docs/eval-sandbox/).

### Added

- **Personal deployments in a shared account** ([named-deployments](docs/eval-sandbox/named-deployments/spec.md)):
  - `scripts/cloud.sh --name <you> …` (or `make … NAME=<you>`) runs each command against your own stacks. No name is the main deployment, unchanged.
  - Each probe's metric is tagged with its deployment, so each alarm watches its own.
  - Named deployments reserve no concurrency and add no budget by default. `destroy --data` removes one completely.
  - `cloud.sh list` shows every deployment in the account.
- **Any account, no edits:** `make deploy` runs the CDK bootstrap when an account lacks it. The laptop stack (`ClayFoundryEvalSandbox`) is optional, created by `make laptop-stack`. Its runs bucket is now `foundry-runs-<account>-us-west-2`, and the Bedrock configs name it with `{account}`: runs copy to the caller's own account's bucket, uploads check the bucket's owner, and an account without one gets a single note instead of a warning per run.
- **A Makefile:** `make bootstrap` sets up a workstation from the pinned versions and ends with `make doctor`'s readiness check. `make check` runs lint, types, the Python tests and the web tests; `make` lists everything. CI runs `make lint` (ruff over src, tests, scripts and infra) as its own job.
- **`scripts/cloud.sh reindex`:** rebuilds the board cards stored in the cloud after a card field is added. It's safe beside live work, and safe to re-run.
- **Batches:**
  - **Cloud batches** write `summary.md`, honour `--eval-config`, and don't re-score a run that's already scored.
  - **On a laptop,** a run started from the UI whose process died shows as `interrupted`.
- **Run it in AWS** ([aws-deploy](docs/eval-sandbox/aws-deploy/handoff.md)):
  - **Hosting:** the whole app runs serverless in one account (us-west-2): Lambda behind a function URL, with S3 and DynamoDB for state and Step Functions for batches. There's no VPC.
  - **Access:** sign-in with named access tokens.
  - **Spend:** a monthly spend cap that reserves a job's cost when it starts and settles it when it ends.
  - **Operations:** `scripts/cloud.sh` deploys, checks both new versions before either goes live, and rolls back. It also manages tokens, logs, stop/start and destroy. Alarms, 30-day logs, and job start and end lines in CloudWatch. The runbook is [docs/deploy.md](docs/deploy.md).
- **`foundry-remote`:** the API from a terminal with only a token. It's one file, served by a deployment at `/cli/foundry-remote.pyz`, and runs on Python 3.9 or later, including macOS's own `python3`. From a clone it's `foundry remote`.
- **[The CLI reference](docs/cli.md)** for both tools: every command, option, output and exit code, plus recipes for scripts and CI ([cli](docs/eval-sandbox/cli/handoff.md)).
- **`foundry batch`:** prompts × variants × repeats, with a cost cap, resuming, and per-model score spread (mean, min, max, stdev).
- **Scoring agents we didn't build:**
  - `foundry import`, an OTLP/JSON trace file
  - `foundry receive`, a live OpenTelemetry receiver
  - `foundry external`, which runs any instrumented agent command and scores its runs

  See [increment 3](docs/eval-sandbox/increment-3.md) and [increment 4](docs/eval-sandbox/increment-4.md).
- **`foundry run --evaluate`:** run and score in one command.
- **A second MCP server:** STAC scene search (Earth Search, no credentials).
- **Workflow score v2:** an optional `sequence` component, from expected tool steps per prompt ([increment 4](docs/eval-sandbox/increment-4.md)).
- **Multi-agent pipeline mode:** a planner, an executor and a checker, shown as agent bands in the trace and as its own dashboard row ([increment 7](docs/eval-sandbox/increment-7/)).
- **Rubric import:** from a PDF, Word, Excel, CSV, Markdown, HTML, JSON, a URL, text or an MCP tool. Drafted by the judge model, reviewed, then activated, with history and rollback (`foundry rubric`, [increment 8](docs/eval-sandbox/increment-8/)).
- **Judge sanity check:** `scripts/judge_sanity.py`, five runs with known verdicts.
- **Model inventory:** every Bedrock model in the account was tested with real calls ([bedrock-models.md](docs/eval-sandbox/bedrock-models.md)).
- **Golden live runs** for offline replay in demos: `fixtures/runs/golden/`.
- **The web app** (TypeScript, over a typed FastAPI JSON API, [increment 9](docs/eval-sandbox/increment-9/)):
  - **Runs:** a live board, and a run monitor with gauges, a waterfall and the step feed.
  - **Other pages:** Prompts, Batches, Dashboard, Rubric, Import and Compare.
  - **Features:**
    - viewing any rubric's contents ([increment 10](docs/eval-sandbox/increment-10.md))
    - board numbers that follow the filters ([increment 11](docs/eval-sandbox/increment-11.md))
    - managing prompts: add, edit, version, archive, expected steps ([increment 13](docs/eval-sandbox/increment-13/))
    - the Foundry logo ([increment 14](docs/eval-sandbox/increment-14/))
    - the full tool menu with descriptions (`foundry tools`, [increment 15](docs/eval-sandbox/increment-15/))
  - **Needs attention:** each card says why its run failed, and "Score it" appears only on unscored runs ([board-attention](docs/eval-sandbox/board-attention/handoff.md)).

### Changed

- **One code path for the laptop and AWS** ([backend-parity](docs/eval-sandbox/backend-parity/handoff.md)):
  - **State:** services read and write through `state.of(ws)`.
  - **Board:** board cards come from one builder (`services/cards.py`).
  - **Job kinds:** declared once (`services/job_kinds.py`).
  - **Batches:** `foundry batch` and the cloud's batch workflow call the same engine functions.
  - **Tests:** the API tests run on both setups, the cloud one starting every request cold.
  - **Rule:** an architecture rule keeps the cloud's store out of services and api.
- **Default live config:** Kimi K3 is the agent and Claude Opus 4.6 the judge, chosen by price and by judge calibration. Cached input tokens count toward `tokens_in` and the input-token cap.
- **Infra:** Bedrock access is scoped to the configured models, the S3 mirror is on by default, and S3 access logs are on ([increment 6](docs/eval-sandbox/increment-6.md)).
- **Wording:** every screen explains itself in plain words. "Evaluate" became "Score" in the UI ([increment 12](docs/eval-sandbox/increment-12/)).
- **Code layout:** `src/foundry` is in five layers (kernel, data, engine, services, API/UI), and a test enforces the import rules ([increment 16](docs/eval-sandbox/increment-16/handoff.md)).
- **API:** typed contracts on every route. Request bodies are strict, so unknown fields are refused ([increment 17](docs/eval-sandbox/increment-17/)).

### Removed

- **The server-rendered classic UI at `/classic`** ([increment 16](docs/eval-sandbox/increment-16/handoff.md)). Use `/`, or `/api/*` from scripts.

### Fixed

- **Stale prompts on a warm cloud instance:** it checked a new run against prompt files fetched at its cold start, so a prompt created on another instance was "unknown". Project files are now fetched before each read.
- **Board order:** the two boards ordered runs that started at the same moment differently. Ties now go by run id.
- **Cloud batches:** a batch whose cost total outgrew a float's precision never finished, and kept holding its slot.
- **`foundry receive`:** it dropped a trace it had already acknowledged when Ctrl-C came within 2 s.
- **`foundry-remote`:** `FOUNDRY_URL` skipped the https check, so a token could be sent in cleartext.
- **Concurrency:** DynamoDB transaction conflicts are retried, and a finished job is never mistaken for contention.
- **Robustness:** an MCP server can't outlive a killed run. Huge or deeply nested rubric sources can't crash drafting.
- **Web app:** tabs no longer submit their form, and a just-started run no longer shows "Run not found".

## 0.1.0 (2026-10-30, `prototype` branch)

First working version of the agentic eval sandbox. See [the spec](docs/eval-sandbox/spec.md).

- **`foundry run`:** runs a Strands agent over MCP tools (the real RODA server) and records a contract-valid run directory. The run directory holds `run.json`, `trace.jsonl` (every model and tool call, with tokens, cost, retries, invalid calls and RODA in-body errors) and `output.json`. Runs are bounded by limits on model calls, input tokens and wall clock.
- **`foundry evaluate`:** produces a deterministic workflow score, a judge-model output score against a rubric, a narrative, and `report.md` with provenance. It works on any contract-valid run directory, which also makes it the replay path.
- **`foundry validate`:** checks a run directory against the trace contract (v0.2).
- **`foundry ui`:** a local score-card UI on 127.0.0.1 that starts runs and evaluations.
- **Providers:** `scripted` and `offline` (no credentials needed) and `bedrock`. Swapping the model is a config change.
- **Tracers:** JSONL (always on) and OpenTelemetry GenAI spans over OTLP/HTTP. Swapping the tracer is a config change.
- **Storage:** an optional S3 mirror of run directories.
- **Infra:** a CDK stack (us-west-2) with a private runs bucket and a least-privilege harness role.
