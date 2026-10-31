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

**Prerequisites**, all installed by asdf (see [Local setup](#local-setup)):

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

The prompts are `adb-001` to `adb-006` (see `tasks/adb/prompts.jsonl`). Run `foundry --help` for every command.

## The UI: everything without the command line

```bash
uv run foundry ui        # http://127.0.0.1:8765
```

| Page | What you can do |
|---|---|
| **Runs** | Start a run (prompt × config); see every run as a score card; open the report; evaluate a run that hasn't been scored; **compare** it with any other run (a score, cost and calls table with deltas, then both timelines). |
| **Batches** | Pick a base config, any number of agent models and prompts, repeats, parallel runs, a cost cap and whether to evaluate. Start the batch, then watch the per-model table (mean, min, max and stdev of the overall score) and per-run rows fill in, each linking to its report. |
| **Import** | Evaluate an agent we didn't build: start the OpenTelemetry receiver with one click and point the agent at the address shown, or upload an OTLP/JSON trace file. Each agent run becomes a normal card. |

- A run's page is a live monitor. While the run is going, it updates in place every second and shows:
  - a ticking clock
  - gauges for model calls, time and input tokens against the run's limits
  - a waterfall of every model and tool call, where parallel calls overlap and errors are red
  - a step feed with the model's text and each tool's arguments and result

  Once evaluated, score rings sit above the report, and the feed folds under it.
- Other pages refresh themselves while something is running.
- Batches keep going if the UI is restarted.
- The UI listens on 127.0.0.1 only.

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
- **Role prompts:** they are versioned in `src/foundry/harness/roles/v1/`, and the version is recorded in `run.json.agents.prompts`.

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
- **STAC** (`src/foundry/mcp_servers/stac.py`, part of this repo): finds actual scenes for an area and date range through `search_items`, `get_item` and `list_collections`. It uses Earth Search by default; set `STAC_API_URL` to use another STAC API. Responses are compacted to a few KB, so they don't flood the agent's context.

Every MCP server runs under a small reaper (`foundry.harness.reaper`), so it can't outlive a killed `foundry run`.

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

## Deploy (live checks)

`infra/` holds a CDK app for one sandbox account in **us-west-2**:

- a private, encrypted, versioned runs bucket
- a role allowed exactly:
  - `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`
  - read and write on `runs/*` in that bucket
  - read-only on `samples/*`

The account comes from your credentials, so anyone can deploy into their own sandbox. The region is fixed at us-west-2.

```bash
cd infra && npm ci                                    # pins the CDK CLI (2.1146.1)
npx cdk synth                                         # no credentials or Docker needed
export AWS_PROFILE=<profile>                          # credentials for the target account
npx cdk bootstrap aws://<account id>/us-west-2        # once per account and region
npx cdk deploy                                        # prints RunsBucketName and HarnessRoleArn
cd ..
scripts/stage_samples.sh      # A-2: copies a small NAIP tile and a Vantor tile to s3://<bucket>/samples/ (~85 MB)
scripts/verify_deploy.sh      # as the deployed role: one live run --evaluate, mirrored to S3, then checked
```

The `config/bedrock*.toml` configs mirror every run and evaluation to the team's deployed bucket, under `s3://<bucket>/runs/<run_id>/`. The offline configs don't mirror, so a fresh clone needs no AWS.

- **Your own deploy:** set `[storage] s3_uri = "s3://<RunsBucketName output>"` in those three configs.
- **Turn mirroring off:** set it to `""`.

Mirroring is best-effort. A failed upload, such as missing credentials or another account's bucket, prints a warning and keeps the local files. `verify_deploy.sh` checks the mirror on a temporary copy of the config.

The role can call only the Bedrock models named in `config/bedrock*.toml`: `[model]`, `[judge]`, `[summarizer]` and the `[prices]` keys. If you add a model to a config, redeploy before using it from the role. The runs bucket's access logs go to a separate bucket and expire after 90 days.

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

## Stack decisions (K-5)

| Area | Decision |
|---|---|
| Agent framework | **Strands Agents** 1.62, behind our own provider and tracer seams |
| Infrastructure as code | **AWS CDK in Python** |
| Tracing | **Our own JSONL** (system of record) plus **OpenTelemetry** GenAI spans as the swappable second tracer |
| UI | **FastAPI and Jinja2**, server-rendered, with no JavaScript build |

The reasons are in [the design](docs/eval-sandbox/design.md#decisions).

## Start here (planning docs)

1. [Requirements](docs/requirements.md): Must, Should, Could and Should-not.
2. [Visuals](docs/visuals.md): the loop, architecture, timeline and checkpoints.
3. [Trace contract](docs/trace-contract.md): the shared file format.
4. [Backlog](docs/backlog.md): work streams and "done when" criteria.
5. [Spec](docs/eval-sandbox/spec.md) and [design](docs/eval-sandbox/design.md) for this build.

Source material from the organizers:

- [Vision](docs/Clay-Foundry-Vision-Hackathon.md)
- [Technical](docs/Clay-Foundry-Technical-Hackathon.md)

## Local setup

Tool versions are pinned in `.tool-versions` (uv, Node, AWS CLI) and `.python-version` (Python, installed by uv).

1. Install [asdf](https://asdf-vm.com) 0.16+ and put its shims on your PATH: `export PATH="${ASDF_DATA_DIR:-$HOME/.asdf}/shims:$PATH"`.
2. From the repo root:

   ```bash
   asdf plugin add uv && asdf plugin add nodejs && asdf plugin add awscli
   asdf install
   uv python install
   ```

Python comes from uv rather than asdf, so nothing needs compiling. asdf would build CPython from source, and 3.12 doesn't build against Homebrew's OpenSSL 4.

`uvx` (part of uv) must be on your PATH, because `foundry run` uses it to start the RODA MCP server.

## License

Apache-2.0. See [LICENSE](LICENSE).
