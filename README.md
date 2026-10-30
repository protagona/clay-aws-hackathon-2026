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

The prompts are `adb-001` to `adb-005` (see `tasks/adb/prompts.jsonl`). Run `foundry --help` for every command.

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

**Overall score** is `0.5 × workflow + 0.5 × output`, configurable in `[scoring]`. It is `null` ("incomplete") when the judge's reply can't be used after one retry.

The exact definitions are in the [spec](docs/eval-sandbox/spec.md#definitions).

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
- `config/<name>.toml` with `[task] id = "<task_id>"`.

## Deploy (live checks)

`infra/` holds a CDK app for one sandbox account in **us-west-2**:

- a private, encrypted, versioned runs bucket
- a role allowed exactly `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`, plus `runs/*` in that bucket

```bash
cd infra && npm ci            # pins the CDK CLI (2.1146.1)
npx cdk synth                 # no credentials or Docker needed
npx cdk deploy                # with credentials for the target account (e.g. AWS_PROFILE=...)
```

Then set `[storage] s3_uri = "s3://<RunsBucketName output>"` to mirror every run and evaluation to `s3://<bucket>/runs/<run_id>/`.

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
