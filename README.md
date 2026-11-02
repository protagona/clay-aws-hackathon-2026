# Clay Earth Dataset Foundry: Agentic Eval Sandbox

Breaking Barriers US 2026 hackathon, Team 46, for Renaissance Philanthropy (Clay).

This is an observability and evaluation sandbox. It shows that an agentic geospatial workflow can be **observed, summarized, and scored** end to end:

1. An agent plans a dataset build by calling geospatial tools on the [RODA MCP server](https://registry.opendata.aws/).
2. Every model call and tool call is recorded as a trace.
3. The trace is summarized and scored on two axes:
   - **Workflow**, computed from the trace alone
   - **Output**, scored by an LLM judge against a rubric
4. A per-run report gives the scores and the run's provenance.

## Quickstart (offline)

Offline mode needs **no AWS credentials**. The agent's decisions come from a recorded script, and a deterministic stand-in plays the judge and summarizer. The tools are real: the agent calls the real RODA MCP server, which needs internet access but no AWS account.

**Prerequisites**, all installed by `make bootstrap` (see [Local setup](#local-setup)):

- uv, which installs Python 3.12
- Node 24, needed only for the CDK infra and its tests

Run everything below from the repo root.

```bash
uv sync                                                         # install dependencies
uv run foundry run --config config/offline.toml --prompt adb-001
# prints the run directory, e.g. runs/2026-10-30T06-40-26Z_adb-001_7bd8
uv run foundry evaluate runs/<that run id>                      # writes scores.json + report.md
uv run foundry run --config config/offline.toml --prompt adb-001 --evaluate   # or both in one command
uv run foundry ui                                               # http://127.0.0.1:8765
```

Open `runs/<run id>/report.md` or the UI to read the report:

- summary
- struggles
- workflow score
- output score with per-criterion rationales
- timeline
- agent output
- provenance

The prompts are `adb-001` to `adb-006` (see `tasks/adb/prompts.jsonl`). Every command, option, output and exit code, plus recipes for scripts and CI, is in the [CLI reference](docs/cli.md); `foundry --help` gives the short version.

## The UI: everything without the command line

```bash
uv run foundry ui        # http://127.0.0.1:8765
```

It's a TypeScript single-page app (React, Vite, Tailwind) over a FastAPI JSON API (`/api`). It updates live as runs progress, with no reloads.

| Screen | What you can do |
|---|---|
| **Runs** | A live board. Runs move through **Running → Being scored → Scored / Needs attention** as they progress, with headline numbers that follow the filters and search. **▶ New run** starts one. A card in Needs attention says why the run failed (a limit hit, a tool server that didn't start, a failed model call), and **Score it →** sends an unscored run to the judge. Click a card for the run panel: scores, limits used, agent bars, timeline, steps, the report and the rubric that scored it. |
| **Prompts** | Every prompt as a card with its run count and average score. Add a prompt, edit one that hasn't been run, make a new version of one that has (its text locks once it has runs), set its expected tool steps (the workflow score's sequence check), or archive it to hide it from New run and batches. Writes go to `tasks/<task>/prompts.jsonl` and `expected.jsonl`, so commit them like any other change. The CLI still accepts archived prompts (`foundry batch --prompts all` includes them). |
| **Batches** | Start a batch (models × prompts × repeats, with a cost cap), then watch the per-model table and the runs fill in. |
| **Dashboard** | Model ranking, cost vs score (hover for details), and where agents struggle, filtered by rubric. |
| **Rubric** | Import a rubric from a file (drag and drop), text, a URL or an MCP tool; review the draft; activate it; roll back. |
| **Import** | Drop an OTLP/JSON trace, or start the live OpenTelemetry receiver and watch runs arrive. |
| **Compare** | Two runs side by side: differences (green when B did better) and both runs' steps (from a run's **Compare ↗**). |

- **Every page explains itself:** a title and one-line caption, then numbered sections, each with a heading and caption.
- **Pop-up notifications** tell you when a run or its scoring finishes.
- **Light and dark** follow the system setting; the toggle remembers your choice.
- **Phone width:** the run monitor becomes a full-screen sheet.
- **One UI:** the server-rendered UI of increments 1–8 (`/classic`) was removed in increment 16; use `/`. Scripts use `/api/*` with the `X-CSRF-Token` header from `GET /api/session` and JSON bodies.
- **Bind address:** the UI listens on 127.0.0.1 only.

**Developing the UI:** the source is in `web/`, and needs Node `^22.22.2 || >=24.15.0`.

```bash
cd web && npm ci
npm run dev       # Vite on :5173, proxying /api to a running `foundry ui`
npm test          # Vitest
npm run build     # writes src/foundry/ui/web_dist/; commit it so `foundry ui` needs no Node
node ../scripts/ui_e2e.mjs /tmp/shots   # end-to-end check in headless Chromium, on its own server and a copy of runs/
```

The CLI below does the same things.

## Importing a rubric (any format)

Clay's rubric might arrive as a PDF, a Word file, a multi-tab Excel workbook, Markdown, a URL, another tool's output, or a few sentences. The **Rubric** tab, or the CLI, turns any of these into the strict `rubric.json`.

```bash
uv run foundry rubric draft --task adb --file "Clay rubric.xlsx"      # or --text "...", --url https://..., --mcp roda/tool --args '{...}'
uv run foundry rubric list --task adb                                 # active version, history (with run counts), drafts
uv run foundry rubric activate --task adb <draft-id>                  # or a history file name to roll back
```

- **Drafting:** the config's judge (Opus 4.6 on Bedrock, `config/bedrock.toml`) drafts the criteria, and notes every interpretation it made. A source that isn't a rubric is refused rather than invented.
- **Review:** you check and edit the draft before activating it.
- **History:** activation keeps every earlier version under `tasks/<task>/rubrics/history/`.
- **Tracking:** each run records the rubric that scored it, and the dashboard filters by rubric so scores from different rubrics aren't mixed.
- **Git:** drafts and the original source files stay local (git-ignored). Commit `rubric.json` and the history.

## Multi-agent mode (planner → executor → checker)

```bash
uv run foundry run --config config/offline-pipeline.toml --prompt adb-002 --evaluate   # offline, no AWS
uv run foundry run --config config/bedrock-pipeline.toml --prompt adb-001 --evaluate   # Bedrock
```

`[agents] mode = "pipeline"` in any config runs the same task as three agents on the same model:

1. **Planner:** has no tools and writes a plan.
2. **Executor:** has the MCP tools and writes the answer.
3. **Checker:** has the same read-only tools and replies `VERDICT: approve` or `VERDICT: revise`.

On `revise`, the executor gets one revision and the checker one more look.

- **Budget:** the limits are shared across all three agents, so a pipeline gets the same budget as a single run.
- **Hitting a limit:** if the run hits a limit after the executor has answered, it still succeeds with that answer.
- **Trace:** each agent writes an `agent_step`, and its calls carry `parent`. The run page shows them as agent bands.
- **Comparing:**
  - The dashboard lists `<model> · pipeline` as its own row.
  - A batch over `bedrock` and `bedrock-pipeline` compares single and multi-agent directly.
- **Role prompts:** they are versioned in `src/foundry/engine/harness/roles/v1/`, and the version is recorded in `run.json.agents.prompts`.

## What gets written

Each run gets its own directory, `runs/<run_id>/`:

| File | Written by | What it is |
|---|---|---|
| `run.json` | `foundry run` | What was asked, with which model, tools and limits, and how it ended (`running` until it ends) |
| `trace.jsonl` | `foundry run` | One line per step: model calls with tokens and cost, and tool calls with arguments, results, errors, `retry_of` and `invalid` |
| `output.json` | `foundry run` | The agent's final answer (`kind: "none"` if there was none) |
| `mcp-<server>.log` | `foundry run` | The MCP server's stderr |
| `rubric.json` | `foundry evaluate` | A byte-for-byte copy of the rubric used |
| `scores.json` | `foundry evaluate` | Workflow and output scores, overall score, narrative, struggles, evaluation cost, and integrity hashes |
| `report.md` | `foundry evaluate` | The human-readable report |

The format is the [trace contract](docs/trace-contract.md). Any harness that writes `run.json`, `trace.jsonl` and `output.json` in that format can be evaluated with `foundry evaluate`. Check a directory with `foundry validate <run_dir>`.

## How scores work

**Workflow score** is computed from the trace alone, with no model involved. It's a weighted sum of five components:

| Component | Weight |
|---|---|
| completion | 0.40 |
| reliability (share of tool calls without errors) | 0.20 |
| recovery | 0.15 |
| validity (share of valid tool calls) | 0.10 |
| efficiency (model calls versus the budget) | 0.15 |

**Output score** comes from a judge model, which scores each rubric criterion from 1 to `max`. Each criterion is normalized and the results are averaged with the criterion weights. The judge only sees the run inside a delimited data block, and its instructions tell it to treat that block as data, never as instructions.

**Sequence (workflow scorer v2):** `tasks/<task>/expected.jsonl` can list the tools a prompt should use, in order, as stages. Each stage is a list of acceptable tools, for example "discover a dataset, then confirm it". When a prompt has stages, the workflow score adds a `sequence` component, the share of stages matched in order by successful calls. The weights then become:

| Component | Weight |
|---|---|
| completion | 0.35 |
| reliability | 0.15 |
| recovery | 0.15 |
| validity | 0.10 |
| efficiency | 0.10 |
| sequence | 0.15 |

Without stages, the v1 weights above apply unchanged. `tasks/adb/expected.jsonl` is our stand-in until Clay's rubric arrives.

**Overall score** is `0.5 × workflow + 0.5 × output`, configurable in `[scoring]`. It is `null` ("incomplete") when the judge's reply can't be used after one retry.

The exact definitions are in the [spec](docs/eval-sandbox/spec.md#definitions).

### Judge sanity check (D-6)

`fixtures/judge-cases/` holds five runs with known verdicts:

- **A:** a strong real plan
- **B:** a short real plan
- **C:** a vague hand-written plan
- **D:** a polished plan built on an invented dataset the trace never confirms
- **E:** the prompt-injection fixture

`scripts/judge_sanity.py` scores each one 3 times through `foundry evaluate`'s own code. It checks four things: each mean lands in its band, A > B > C, D's data-fit and feasibility stay at 1, and repeats agree. That means the output score spreads by at most 0.25, and each criterion by at most 1 point.

```bash
AWS_PROFILE=<profile> uv run python scripts/judge_sanity.py --config config/bedrock.toml   # exit 0 = all checks pass
```

Result on 2026-10-30, Opus 4.6 judge, $0.36. **All 13 checks pass.**

| Case | Output score (3 repeats) | Expected |
|---|---|---|
| A strong | 0.92 / 0.92 / 1.00 | high |
| B short | 0.42 / 0.42 / 0.42 | mid |
| C vague | 0.08 / 0.08 / 0.08 | low |
| D hallucinated | 0.00 / 0.00 / 0.00 | low; data-fit and feasibility at 1 |
| E injection | 0.17 / 0.17 / 0.17 | low |

**Known issues:**
- This shows the judge separates obvious cases and is stable. It doesn't show that it agrees with human graders on close calls.
- All five cases share one task and our stand-in rubric. When Clay's rubric arrives, rerun the script with it.
- Cases C and D reuse real traces with hand-written outputs.
- Earlier judge candidates and why they were dropped are in [bedrock-models.md](docs/eval-sandbox/bedrock-models.md#judge-calibration-2026-10-30).

## Tools the agent can use

Two MCP servers are configured, and both need no credentials:

- **RODA** (`awslabs.roda-mcp-server`, pinned): discovers datasets in the Registry of Open Data on AWS.
- **STAC** (`src/foundry/engine/mcp_servers/stac.py`, part of this repo): finds actual scenes for an area and date range through `search_items`, `get_item` and `list_collections`. It uses Earth Search by default; set `STAC_API_URL` to use another STAC API. Responses are compacted to a few KB, so they don't flood the agent's context.

Every MCP server runs under a small reaper (`foundry.engine.harness.reaper`), so it can't outlive a killed `foundry run`.

## Evaluate an agent we didn't build

Any agent that emits OpenTelemetry GenAI spans can be scored without changing it. That includes Strands' built-in telemetry, and other frameworks that follow the GenAI semantic conventions.

**One command**, with `foundry external`. It does four things:

1. Starts a private receiver on 127.0.0.1.
2. Runs your command, as an argument list with no shell, with these set:
   - `OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`
   - `FOUNDRY_PROMPT`, the prompt text
3. Waits for the command to exit, then writes one run directory per agent run.
4. With `--evaluate`, scores each run.

```bash
AWS_PROFILE=<profile> uv run foundry external --task adb --prompt adb-001 --config config/bedrock.toml --evaluate \
    -- uv run python examples/external_strands_agent.py
```

**Behaviour:**
- **Output:** stdout carries only run directories and score lines. The agent's own output goes to stderr.
- **Exit codes:** 0 when the command succeeded and every run succeeded; 1 for a failed run or command; 2 for no runs received, usage errors, or an evaluation error.
- **Timeout:** `--timeout` (default 600 s) kills the command.
- **Trust:** the command runs as you, unsandboxed, like anything you type in a shell.

**Step by step**, or from a file:

```bash
# live: the agent exports straight to us
uv run foundry receive --task adb --prompt-id adb-001 --config config/bedrock.toml     # 127.0.0.1:4318
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 <run the agent>                     # e.g. examples/external_strands_agent.py
# or from a file (OTLP/JSON, e.g. the OpenTelemetry Collector file exporter)
uv run foundry import traces.jsonl --task adb --prompt-id adb-001
# then, as for any run
uv run foundry evaluate runs/<run id> --config config/bedrock.toml
```

Each `invoke_agent` span becomes one run: model calls, tool calls with arguments, results and errors, tokens, cost (with `--config` prices), the prompt and the final answer. Details and limitations are in [increment-3.md](docs/eval-sandbox/increment-3.md).

## Batches: many runs, one table

```bash
uv run foundry batch --config config/bedrock.toml \
    --models us.moonshotai.kimi-k3,us.anthropic.claude-sonnet-4-6 \
    --prompts adb-001,adb-006 --repeats 1 --concurrency 2 --evaluate --max-cost 2 --out batches/demo
```

- Each job is a real `foundry run`, plus `foundry evaluate` with `--evaluate`. At most `--concurrency` jobs run at once.
- `--models` derives one config per model by changing only `[model] id`. The derived configs are saved in `<out>/configs/`.
- `--max-cost` stops launching new runs once the recorded spend reaches it.
- Re-running the same command resumes, and crashed runs are retried.
- Results go to `<out>/summary.md` and `<out>/summary.csv`. `summary.md` gives per-variant aggregates: the mean, min, max and sample stdev of the overall score across repeats and prompts, which is how sensitive a model is (D-7).

Example: the command above ran 4/4 runs in 83 s for $0.79.

## Swap the model (no code change)

The model is chosen by the `[model]` section of the config:

```bash
uv run foundry run --config config/offline.toml     --prompt adb-001   # scripted "model" A
uv run foundry run --config config/offline-alt.toml --prompt adb-001   # scripted "model" B: only [model] differs
```

On Bedrock, with credentials:

- `config/bedrock.toml` (the default): **Kimi K3 as the agent, Opus 4.6 as the judge**. The cheaper model takes the token-heavy role. Measured at about $0.16 per run plus evaluation (design ADR-11).
- `config/bedrock-alt.toml`: identical except the agent is **Claude Sonnet 4.6**. Use it for the model-swap demo.
- `config/bedrock-otel.toml`: identical except it also exports OpenTelemetry spans. Use it for the tracer-swap demo.

Which models can actually run the agent in this account? See [docs/eval-sandbox/bedrock-models.md](docs/eval-sandbox/bedrock-models.md), produced by calling every model. Re-check after quota or access changes with:

```bash
AWS_PROFILE=<profile> uv run python scripts/bedrock_model_sweep.py /tmp/models.json
```

```bash
uv run foundry run --config config/bedrock.toml     --prompt adb-001
uv run foundry run --config config/bedrock-alt.toml --prompt adb-001
uv run foundry evaluate runs/<run id> --config config/bedrock.toml
```

**Providers:**

- `bedrock`
- `scripted`, which replays `tasks/<task>/scripts/<model>/<prompt>.json`
- `offline`, judge and summarizer only

The judge and summarizer have their own `[judge]` and `[summarizer]` sections, so you can, for example, score offline runs with a Bedrock judge.

## Swap the tracer (no code change)

`trace.jsonl` is always written, because it's the record evaluation reads. Add `"otel"` to send the same steps as OpenTelemetry GenAI spans to any OTLP/HTTP backend:

```bash
uvx --from arize-phoenix phoenix serve                                      # local Phoenix on :6006, no account needed
uv run foundry run --config config/offline-otel.toml --prompt adb-001
# open http://localhost:6006 to see the run's spans
```

For a backend that needs auth, such as Langfuse at `/api/public/otel/v1/traces`:

1. Put the headers in an environment variable, e.g. `export LANGFUSE_OTLP="Authorization=Basic …"`.
2. Set `headers_env = "LANGFUSE_OTLP"` in `[tracing.otel]`.

The header value is never written to any file. If the endpoint is down, the run still completes, with one warning.

## Demo

The run-of-show, with commands, timings, talking points and fallbacks, is in [docs/demo.md](docs/demo.md). It was rehearsed end to end on 2026-10-30. Recorded live runs for offline replay are in `fixtures/runs/golden/`: `uv run foundry ui --runs-dir fixtures/runs/golden`.

## Replay (when the live agent fails)

`foundry evaluate` never runs the agent. Any saved run directory can be scored, or re-scored, on its own:

```bash
uv run foundry evaluate fixtures/runs/recovered --config config/offline.toml   # hand-made fixture
uv run foundry evaluate runs/<old run id> --rubric path/to/clay-rubric.json     # re-score against another rubric
```

Re-evaluating replaces `rubric.json`, `scores.json` and `report.md` together and never touches `run.json`, `trace.jsonl` or `output.json`. If a run crashed and is stuck at `running`, `--force` evaluates it as failed.

## Bring your own task, rubric or prompts

Add files only; no Python changes are needed:

- `tasks/<task_id>/prompts.jsonl`: one `{"prompt_id", "prompt"}` per line. Ids must match `[A-Za-z0-9._-]`.
- `tasks/<task_id>/rubric.json`: criteria with `id`, `name`, `description`, `max` (2 or more), optional `weight`, and `levels` describing at least 1 and `max`. See `tasks/adb/rubric.json`, which is a stand-in until Clay's rubric arrives.
- `tasks/<task_id>/system.txt`: the agent's system prompt.
- `tasks/<task_id>/expected.jsonl` (optional): one `{"prompt_id", "stages": [[tool, ...], ...]}` per line, the expected tool order for the workflow score's `sequence` component. Use bare tool names, because imported runs don't record which server a tool came from.
- `config/<name>.toml` with `[task] id = "<task_id>"`.

## Run it in AWS (a shared URL)

The whole app (web, API, runs, scoring, batches, rubric drafts) also runs serverless in one AWS account in **us-west-2**: Lambda behind a public function URL, S3 and DynamoDB for state, Step Functions for batches. There is no VPC and no server. People sign in with a named access token, and a monthly cap ($30 by default) limits model spend.

```bash
export AWS_PROFILE=<profile>
scripts/cloud.sh preflight
scripts/cloud.sh deploy --email you@example.com   # prints the URL
scripts/cloud.sh token add <name>                 # prints the token once; send it privately
```

Anyone with a token can also use the API from a terminal without a clone: the deployment serves a one-file client at `/cli/foundry-remote.pyz` (from a clone: `uv run foundry remote`). Its commands are in the [CLI reference](docs/cli.md#foundry-remote-reference).

Several people can deploy into one account: `scripts/cloud.sh --name <you> deploy` gives you your own deployment next to the team's main one ([Personal deployments](docs/deploy.md#personal-deployments-several-people-one-account)).

The runbook covers first deploys, day-to-day commands, alerts, restoring data and troubleshooting: [docs/deploy.md](docs/deploy.md). The spec, design and verification record are in [docs/eval-sandbox/aws-deploy/](docs/eval-sandbox/aws-deploy/handoff.md).

## The account's laptop stack (live checks, `ClayFoundryEvalSandbox`)

This is the earlier stack (`FoundryStack`, `infra/app.py`), separate from the one above: a bucket and a role for running live checks from a laptop. `cloud.sh` never touches it; `make account-setup` creates it.

`infra/` holds a CDK app for one sandbox account in **us-west-2**:

- a private, encrypted, versioned runs bucket
- a role allowed exactly:
  - `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`
  - read and write on `runs/*` in that bucket
  - read-only on `samples/*`

The account comes from your credentials, so it works the same in the team's shared account and in each person's own sandbox. The region is fixed at us-west-2.

```bash
export AWS_PROFILE=<profile>  # credentials for the target account
make account-setup            # once per account: CDK bootstrap if missing, this stack, the samples (A-2, ~85 MB)
scripts/verify_deploy.sh      # as the deployed role: one live run --evaluate, mirrored to S3, then checked
```

- **Every `make deploy` runs it first** when the account has no bootstrap or no laptop stack, so the first person to deploy (main or `NAME=<you>`) in any account sets it up. Afterwards the check takes a second.
- **Re-apply it** with `make account-setup` after adding a model to a config (the role allows only the configured models). The first run asks you to approve the IAM changes; `YES=1` skips the prompt.
- **Synthesize without AWS:** `cd infra && npx cdk synth` needs no credentials or Docker.

The runs bucket is `foundry-runs-<account>-us-west-2`, one per account. The `config/bedrock*.toml` configs name it as `s3://foundry-runs-{account}-us-west-2`, and mirror every run and evaluation under `runs/<run_id>/`:
- **No edits per account:** the mirror fills in `{account}` from the credentials the run uses, so each person's runs go to their own account's bucket.
- **Ownership is checked:** the upload requires the bucket to belong to that account, so a bucket of that name owned by someone else is refused.
- **Turn mirroring off:** set `s3_uri` to `""`. The offline configs don't mirror, so a fresh clone needs no AWS.

Mirroring is best-effort. A failed upload, such as missing credentials or an account with no laptop stack, prints a warning and keeps the local files. `verify_deploy.sh` checks the mirror on a temporary copy of the config.

**Deleting it:** the stack keeps both buckets when deleted, so a later `make account-setup` in that account fails on the existing runs bucket name until you empty and delete the buckets (or deploy again before deleting the stack).

The role can call only the Bedrock models named in `config/bedrock*.toml`: `[model]`, `[judge]`, `[summarizer]` and the `[prices]` keys. If you add a model to a config, run `make account-setup` before using it from the role. The runs bucket's access logs go to a separate bucket and expire after 90 days.

**Pre-staged samples** (A-2): the agent reads data at the source through MCP. These copies are only a fallback, for when Requester Pays costs or latency matter.

| Path | Source | License |
|---|---|---|
| `samples/naip/m_3611919_nw_11_060_20220702.tif` | `s3://naip-visualization/ca/2022/60cm/rgb/36119/…`: NAIP 2022, 60 cm RGB, Fresno County, CA (54 MB) | public domain |
| `samples/vantor/B120001101250A10.tif` | `s3://vantor-opendata/events/Nepal-Flooding-Aug-2026/…`: Vantor open data (30 MB) | CC BY-NC 4.0 |

Before the first live run, enable model access in the Bedrock console (us-west-2) for the models in `config/bedrock.toml`. The live checks are L1 to L5 in the [spec](docs/eval-sandbox/spec.md#live-checks).

**Cost guardrails, per live run** (`config/bedrock*.toml`):

- at most 12 model calls
- at most 200k input tokens
- 4,096 output tokens per agent call
- 300 s wall clock

The worst case is about $1.87 per run at Sonnet 4.6 prices. Measured live runs cost $0.04 to $0.30. Prices in `config/bedrock*.toml` come from the AWS Price List API.

## Tests

```bash
uv run pytest                     # everything; the `network` tests call the real RODA server
uv run pytest -m "not network"    # fully offline
```

**Where code goes:** `src/foundry` is split into layers (data, engine, services, api, ui), and a test fails when an import crosses them the wrong way. [docs/architecture.md](docs/architecture.md) has the rules and where new code belongs.

## Stack decisions (K-5)

| Area | Decision |
|---|---|
| Agent framework | **Strands Agents** 1.62, behind our own provider and tracer seams |
| Infrastructure as code | **AWS CDK in Python** |
| Tracing | **Our own JSONL** (system of record) plus **OpenTelemetry** GenAI spans as the swappable second tracer |
| UI | **FastAPI** JSON API serving a prebuilt TypeScript app (React, Vite) |

The reasons are in [the design](docs/eval-sandbox/design.md#decisions).

## Start here (planning docs)

1. [Requirements](docs/requirements.md): Must, Should, Could and Should-not.
2. [Visuals](docs/visuals.md): the loop, architecture, timeline and checkpoints.
3. [Trace contract](docs/trace-contract.md): the shared file format.
4. [Backlog](docs/backlog.md): work streams and "done when" criteria.
5. [Spec](docs/eval-sandbox/spec.md) and [design](docs/eval-sandbox/design.md) for this build.
6. [CLI reference](docs/cli.md): both command-line tools, and how to use them from scripts and CI.
7. [AWS deployment](docs/eval-sandbox/aws-deploy/spec.md): its spec, [design](docs/eval-sandbox/aws-deploy/design.md) and [handoff](docs/eval-sandbox/aws-deploy/handoff.md); the runbook is [docs/deploy.md](docs/deploy.md).

Source material from the organizers:

- [Vision](docs/Clay-Foundry-Vision-Hackathon.md)
- [Technical](docs/Clay-Foundry-Technical-Hackathon.md)

## Local setup

Tool versions are pinned in `.tool-versions` (uv, Node, AWS CLI) and `.python-version` (Python, installed by uv).

1. Install [asdf](https://asdf-vm.com) 0.16+ and put its shims on your PATH: `export PATH="${ASDF_DATA_DIR:-$HOME/.asdf}/shims:$PATH"`.
2. From the repo root, run `make bootstrap`. It:
   - adds the asdf plugins and installs this repo's pinned uv, Node and AWS CLI, without touching tools pinned elsewhere
   - installs Python with uv
   - installs the Python, web and infra dependencies
   - ends with `make doctor`, which checks each tool and version and says "Ready."

   It's safe to run again. `make doctor` alone re-checks a workstation; AWS credentials and podman are reported but optional (Bedrock configs and deploys only).

`make` lists the day-to-day targets:
- `make check` runs lint, the type check, the Python tests and the web tests.
- `make test-all` adds the tests that need internet.
- CI (`.github/workflows/ci.yml`) runs the same checks on every push: `make lint`, every Python test (internet ones included), and the web tests, type check and build. It also checks that the committed web bundle matches its source.
- `make ui`, `make types`, `make web-build`, and `make deploy` / `status` / `logs` pass through to `scripts/cloud.sh`.

Python comes from uv rather than asdf, so nothing needs compiling. asdf would build CPython from source, and 3.12 doesn't build against Homebrew's OpenSSL 4.

`uvx` (part of uv) must be on your PATH, because `foundry run` uses it to start the RODA MCP server.

## License

Apache-2.0. See [LICENSE](LICENSE).
