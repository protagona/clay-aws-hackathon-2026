# Agentic Eval Sandbox: Design

**Spec:** ./spec.md   **Last updated:** 2026-10-29
**Status:** Current

## Overview

This is one Python package, `foundry`, with four CLI verbs: `run`, `evaluate`, `validate` and `ui`.

`run` drives a Strands agent whose tools come from MCP servers. RODA 0.1.7 runs over stdio. Strands hook callbacks turn every model call and tool call into a trace step. Each step goes to the tracers: the JSONL tracer, which writes the run directory and is always on, and optionally an OpenTelemetry tracer that exports the same steps as GenAI spans.

`evaluate` reads only the run directory. It scores the workflow deterministically from the trace, asks a judge model to score the output against the rubric, asks a summarizer model for a narrative, and writes `scores.json`, `rubric.json` and `report.md`.

Models are chosen by `provider` in TOML config:

- `bedrock`: Claude or Nova on Bedrock.
- `scripted`: replays a JSON script.
- `offline`: a deterministic judge and summarizer.

Swapping the model or the tracer is therefore a config change. A FastAPI app renders runs as score cards and reports, and starts runs by calling the same CLI as a subprocess. A CDK app in `infra/` declares the runs bucket and the harness role.

We chose this approach because the run directory is the only interface between the agent side and the evaluation side, which is exactly what [trace-contract.md](../trace-contract.md) set out to make true. Evaluation then works the same whether the run came from our harness, a fixture, or Clay's harness.

## Experience

### Design review (UI)

**Approved** by the user on 2026-10-29.

- **Options shown:**
  - **A**, two pages (runs table, then report). Recommended at first.
  - **B**, split view (list on the left, report on the right).
  - **C**, score cards.
- **Chosen:** C.
- **Refinements the user approved:**
  - each card shows its start time
  - an empty state
  - the report page uses A's full-width layout
  - added on 2026-10-30 at the user's choice, to meet AC24: a card footer showing task and short run id. The full run id is on the report page.

```
GET /
+------------------------------------------------+
| Foundry Eval Sandbox                           |
| Start a run [adb-001 v] [offline v] [Run]      |
+------------------------------------------------+
| +-----------+  +-----------+  +-----------+    |
| | adb-001   |  | adb-002   |  | adb-003   |    |
| | scripted  |  | scripted  |  | sonnet    |    |
| |   0.74    |  |   0.31    |  |  running  |    |
| | W.82 O.66 |  | W.40 O.22 |  |           |    |
| | succeeded |  | failed    |  |           |    |
| | 10-29 14:02| | 10-29 13:40| | 10-29 14:10|    |
| | adb · …a1b2| | adb · …c3d4| | adb · …e5f6|    |
| +-----------+  +-----------+  +-----------+    |
+------------------------------------------------+
Card score: overall | "not evaluated" | "incomplete"
Cards are newest first and wrap to 1 column on narrow screens.
The footer line shows the task and the last 4 characters of the run id.

GET /runs/<id>
+------------------------------------------------+
| < Runs   adb-001 · scripted · succeeded        |
| Overall 0.74 | Workflow 0.82 | Output 0.66     |
+------------------------------------------------+
| Summary · Struggles · Timeline ·               |
| Output scores · Provenance   [Evaluate]*       |
+------------------------------------------------+
* Evaluate shown only when not yet evaluated
```

### UI states

| State | What the user sees |
|---|---|
| No runs | `No runs yet. Start one above or run "foundry run --config config/offline.toml --prompt adb-001".` |
| Run in progress | The card shows `running` instead of a score. The report page shows the timeline so far. Evaluate is disabled, with `If this run crashed, evaluate it from the CLI with --force.` |
| Not evaluated | The card shows `not evaluated`. The report page shows the timeline and an Evaluate button. |
| Evaluated | The card shows the overall score plus W and O. The report page shows the rendered report. |
| Output not scored | The card shows `incomplete`. The report page shows `Output not scored: <reason>`. |
| A run is already in progress when Run is clicked | The page answers 409 with `A run is already in progress: <run_id>` and a link back to `/`. |
| Bad form input | The page answers 400 and names the field, e.g. `Unknown prompt id 'x' for task 'adb'. Available: adb-001, …`, with a link back to `/`. |
| Unknown or invalid run id | 404 `Run not found`, with a link back to `/`. |
| Background job failed | The UI keeps an in-memory table of the jobs it started (kind, run id, PID, start time, and exit code once known). Each subprocess's stderr goes to `runs/.ui-jobs/<job-id>.log`. The banner appears when a `run` job exits 2, which means it was rejected before any run directory existed (e.g. a missing `uvx` or a bad config), or when an `evaluate` job exits non-zero. A `run` that exits 1 is an ordinary failed or timed-out run: it has a run directory and shows up as a card, with no banner. In those cases the index shows a dismissible banner at the top: `Run adb-001 failed to start: <last stderr line>` or `Evaluation of <run_id> failed: <last stderr line>`. The full log is linked as `/jobs/<job-id>/log`, served as plain text under the same id rules. |
| Evaluation in progress | `POST /runs/<id>/evaluate` **takes the `.evaluating` lock in the UI process** before it spawns `foundry evaluate --lock-held <token>`. The subprocess checks that the lock contains that token instead of creating the lock itself. It then redirects (303) to the report page. While the `.evaluating` lock exists, the page shows `Evaluating… reload to see the report.` and the card shows `evaluating`. A second evaluate gets 409. |
| Unreadable `run.json` (corrupt, or another harness's partial write) | The card shows the directory name and `unreadable run.json`, with no link. The rest of the list still renders. |
| Interrupted evaluation (`rubric_sha256` mismatch) | The card shows `incomplete`. The report page shows `Evaluation was interrupted; re-run foundry evaluate.` and the Evaluate button. |

There is no client-side JavaScript. The runs list does not auto-refresh: the user reloads the page. That is enough for a local demo tool and removes a whole class of XSS surface.

### CLI

```
foundry run --config <file> --prompt <prompt_id> [--runs-dir runs] [--run-id <id>]
    --run-id: caller-chosen id (must match ^[A-Za-z0-9._-]+$ and not already exist); the UI uses it to know the id up front
    prints: run directory path; exit 0 succeeded | 1 failed/timeout | 2 usage/config error
foundry evaluate <run_dir> [--config config/offline.toml] [--rubric <path>] [--force] [--lock-held <token>]
    --lock-held: internal, used by the UI, which already holds the .evaluating lock with this token
    exit 0 wrote scores+report | 2 usage error, invalid rubric/config, run still running, invalid run dir, lock held
foundry validate <run_dir>
    prints one line per violation: <file>:<line>: <field>: <problem>; exit 0 valid | 1 invalid
foundry ui [--port 8765] [--runs-dir runs]
    binds 127.0.0.1 only; prints "Foundry UI on http://127.0.0.1:8765"
```

Error messages always name the file, key or id involved and say what to do next. For example:

- `Config config/x.toml: [judge] provider must be one of bedrock, scripted, offline (got 'foo').`
- `Run runs/… is still running. If it crashed, pass --force to evaluate it as failed.`

## Architecture

```
src/foundry/
  cli.py              argparse entry point (console script "foundry")
  __main__.py         enables `python -m foundry` (the UI spawns this)
  config.py           TOML -> pydantic Config; validation errors name file + key
  contract.py         pydantic models for run.json / trace step / output / scores; validator
  runstore.py         run-dir create/append/rewrite (atomic writes), run id generation
  models/
    base.py           Provider protocol: strands_model(), complete(CompletionRequest) -> Completion
    bedrock.py        BedrockModel (strands) + boto3 Converse complete(); botocore timeouts/retries
    scripted.py       ScriptedModel(strands Model) replaying a JSON script; complete() replays too
    offline.py        deterministic judge/summarizer complete()
    registry.py       provider name -> class
  harness/
    mcp.py            build Strands MCPClient per [mcp.servers.*]; absolute command; env allowlist
    hooks.py          TraceHooks(HookProvider): model/tool events -> Step; limits; retry_of; invalid
    runner.py         orchestrates one run: run dir, tracers, MCP, agent, watchdog, finalize
  tracing/
    base.py           Tracer protocol: start_run / record_step / end_run
    jsonl.py          JsonlTracer (always on; writes trace.jsonl)
    otel.py           OtelTracer: GenAI-semconv spans, OTLP/HTTP exporter, failure-tolerant
  evaluate/
    view.py           capped run view (20k chars output, 200-step timeline)
    workflow.py       workflow score v1 (pure function of trace + run.json)
    judge.py          judge prompt, JSON parse + validation, one retry
    summarize.py      narrative prompt, word cap, one retry; deterministic Struggles
    report.py         report.md builder with escaping
    evaluate.py       orchestrates evaluate; atomic write of rubric/scores/report together
  storage.py          S3 mirror (lazy boto3 client; only if s3_uri set)
  templates/          judge-v1.txt, summarizer-v1.txt (versioned prompt templates)
  ui/
    app.py            FastAPI app factory; host/origin/CSRF middleware; routes
    render.py         markdown-it-py renderer: html off, images off, safe link schemes
    templates/        Jinja2: base, index (cards), run
config/               offline.toml, offline-alt.toml, offline-otel.toml, bedrock.toml, bedrock-alt.toml
tasks/adb/            prompts.jsonl, rubric.json, scripts/<model-variant>/<prompt_id>.json
fixtures/runs/        clean, recovered, failed, injection, hostile (hand-written)
infra/                CDK app (app.py, stack.py, cdk.json, package.json pinning the CDK CLI)
tests/                pytest; tests/mcp_fault_server.py (fault-injection MCP server)
```

This follows the existing repo conventions where there are any: [trace-contract.md](../trace-contract.md) owns the file formats, and the backlog defaults are Strands Agents, CDK in Python, and JSONL plus OTel. Everything else is new.

### Path resolution

Every relative path in config and every convention path (`config/`, `tasks/`, scripts) resolves against the **project root**: `$FOUNDRY_HOME` if set, otherwise the current working directory. The README tells users to run `foundry` from the repo root. Prompt templates are package data (`src/foundry/templates/`), loaded with `importlib.resources`. A `run_dir` argument is a filesystem path as given. `foundry evaluate` finds `tasks/<task_id>/rubric.json` under the project root.

### One run

1. `cli run` loads config, resolves the prompt (AC1), and creates `runs/<run_id>/`. When `prompts.jsonl` is loaded, each `prompt_id` must match `^[A-Za-z0-9._-]{1,64}$` and not contain `..`. A bad id is a config error (exit 2) that names the file and line, because the id becomes part of the run id and the path. The run id is `<UTC %Y-%m-%dT%H-%M-%SZ>_<prompt_id>_<4 hex>`. It then writes `run.json` with `status: "running"` and `limits`.
2. The runner builds the tracers. JSONL is always on, and OTel is added if it appears in `[tracing] tracers`.
3. The runner starts each MCP server through `MCPClient(lambda: stdio_client(StdioServerParameters(command=<abs>, args=…, env=<allowlist>)), startup_timeout=mcp_start_timeout_s)`.
   - It calls `list_tools_sync()` and builds a `tool name → server` map plus each tool's input JSON schema.
   - If a server fails to start, the runner records a `note` step, finalizes the run as `failed`, and exits 1 (AC9c).
4. The runner builds `Agent(model=provider.strands_model(), tools=…, system_prompt=<task system prompt>, hooks=[TraceHooks], callback_handler=None)`.
5. **Threading.** The agent runs in a **daemon worker thread**, and the main thread waits on it with `join(timeout)`. At `max_seconds`, the main thread calls `agent.cancel(message="max_seconds reached")`, marks the run as timed out, and waits up to 3 more seconds.
   - If the worker still hasn't returned, the runner records `note: hard timeout`, closes the tracer and writes the final `run.json` and `output.json` **first**. Then it stops each `MCPClient` in a daemon thread joined with a 2 s timeout, so the subprocesses are terminated after the bound has been met. The worker thread is abandoned, and process exit kills it.
   - **Late steps:** finalize first calls `Tracer.close()`. That sets a `closed` flag under the tracer lock, so any step a late worker thread tries to record afterwards is dropped and never appended after `run.json` is final.
   - **AC9's "within T + 5 s"** is measured from run start to the final `run.json` write, and that write happens before the OTel flush and the S3 upload.
   - **Model retries:** we construct the Agent with `retry_strategy=None`, which turns off Strands' built-in retry (by default 6 attempts on `ModelThrottledException`). Transport retries happen only inside botocore. So one adapter invocation is exactly one Before/After model event pair and one `llm_call` step.
6. TraceHooks callbacks:
   - **`BeforeModelCallEvent`:** cancels the call when either limit is about to be crossed:
     - `model_calls` would exceed `max_model_calls`
     - `input_tokens_so_far + event.projected_input_tokens` would exceed `max_input_tokens`

     In either case it sets `event.cancel = "<limit> (<value>) reached"`, records a `note` step, marks the limit as hit, and sets `self._cancelled_call = True`. Strands then fires an `AfterModelCallEvent` for the cancelled call, with zero usage and the cancel text as an `end_turn` message, and the agent finishes its turn. TraceHooks records no `llm_call` step for that event, so the trace never holds more than N `llm_call` steps (AC8). Finalize sees the limit flag, writes `output.json` as `{"kind":"none","text":""}` rather than the cancel text, and sets the status to `failed`. Otherwise `model_calls` goes up by one.
     - **Source:** strands-agents 1.62.0 `hooks/events.py:318-344`, where `BeforeModelCallEvent` has `projected_input_tokens: int | None` and a writable `cancel: bool | str`, and `event_loop/event_loop.py:549-575`, which on cancel builds an `AfterModelCallEvent` with an `end_turn` message holding the cancel text and zero usage. Read 2026-10-29.
     - **Fallback** when `projected_input_tokens` is `None` (the estimate failed): the cap is checked against the cumulative `tokens_in` seen so far, plus the previous call's `tokens_in`, used as the projection.
   - **`AfterModelCallEvent`:** records an `llm_call` step.
     - `tokens_in` and `tokens_out` come from `stop_response.message["metadata"]["usage"]`.
     - The duration is measured from the paired Before event using a monotonic clock. `latencyMs` is not used because the scripted model fakes it.
     - `cost_usd` comes from `[prices]` and is `null` if the model has no price entry.
     - `text` is the assistant text, truncated to 1 KB.
     - If `exception` is set, the step gets `status: "error"` and `error = type(exc).__name__ + ": " + str(exc)[:500]` (AC9b).
   - **`BeforeToolCallEvent`:**
     - If `selected_tool is None`, the step is flagged `invalid` (unknown tool).
     - Otherwise the input is validated against the tool's JSON schema with `jsonschema`. On failure, `event.cancel_tool = "Invalid arguments for <tool>: <first error>"` is set, so the model gets an error result, and the step is flagged `invalid` (AC9d).
       - **Sources (strands 1.62.0):** `hooks/events.py:242-245` (`cancel_tool` is writable) and `tools/executors/_executor.py:223-225` (a cancelled tool produces an error result with that message). `AfterToolCallEvent.result` is writable (`events.py:308-309`), which the 8,000-character cap relies on. `MCPClient(startup_timeout=…)` is at `tools/mcp/mcp_client.py:312`, enforced by `_init_future.result(timeout=…)` at `:468` and raised as `MCPClientInitializationError` at `:481`, which the spike observed. Slice 3 tests confirm each one.
     - The start time is recorded.
   - **`AfterToolCallEvent`:** records a `tool_call` step.
     - `server` comes from the map.
     - `args` is the tool input.
     - `result_preview` is the first 1 KB of the content text.
     - `duration_ms` is `event.duration × 1000`.
     - `status` is `error` if any of these hold:
       - `result.status == "error"`
       - `exception` is set
       - the server's `error_detection = "json_status"` and the content text parses to a JSON object with `"status": "error"`

       The last rule covers RODA, which reports domain errors such as `Dataset not found` inside a successful MCP result. This was observed in the spike on 2026-10-29.
     - `retry_of` is the most recent earlier errored step with the same (server, name, canonical JSON args).
     - A tool cancelled by the watchdog gets `error = "cancelled: run timed out"` (AC9).
     - The tool result passed back to the agent is truncated to 8,000 characters by an `AfterToolCallEvent` callback that rewrites `event.result`. This is the spec's non-goal mitigation.
7. Every step goes to `Tracer.record_step(step)`. The JSONL tracer appends and flushes one line per step.
8. Finalize:
   - The agent's final text goes to `output.json` as `{"kind":"text","text":…}`, or `{"kind":"none","text":""}` when there's no final answer.
   - `run.json` is rewritten with status and `ended_at`.
   - The final status is the first that applies:
     1. `timeout` if the watchdog fired
     2. `failed` if `max_model_calls` or `max_input_tokens` was hit, the model raised, or MCP failed
     3. `succeeded` if the agent returned a final answer
     4. `failed` otherwise
   - Then `Tracer.end_run`, then the S3 mirror (AC20).

### One evaluation

1. `cli evaluate` validates the run directory (AC10d) and loads the evaluate config (AC10e). It refuses a `running` run unless `--force` is given.
   - **Crash tolerance:** when `run.json.status` is `running`, the validator accepts a final `trace.jsonl` line that fails to parse, which is what a process killed mid-append leaves behind, and reports it as a warning, not a violation. `--force` drops that line, and the report says `1 truncated trace line ignored`.
   - **Lock:** evaluation takes `<run_dir>/.evaluating`, created with `O_CREAT|O_EXCL`. The file holds JSON `{"pid": <int>, "token": "<hex>", "owner": "cli"|"ui", "created_at": "<iso>"}`. A second evaluator exits 2 with `Run <id> is already being evaluated.` (the UI answers 409).
     - A lock is stale **only** when its `pid` is no longer alive, checked with `os.kill(pid, 0)`. There is no age threshold, because a live Bedrock evaluation can take more than 20 minutes.
     - A CLI evaluator removes the lock in a `finally` block.
     - **UI-owned locks:** the UI writes the lock with its own PID and a fresh token, then starts `foundry evaluate --lock-held <token>`. A monitor thread waits on that subprocess and removes the lock when it exits, with any exit code, as long as the token still matches. If the UI process itself dies, its PID is dead, so the lock is stale.
2. It loads the rubric (from `--rubric`, or `tasks/<task_id>/rubric.json`) and validates it against the rubric schema in `contract.py`. Before any model call, it warns if the loaded `rubric_id` doesn't match `run.json`'s.
3. `workflow.score(run, steps)` computes the workflow score v1 exactly as defined in the spec's *Definitions*.
4. `view.build(run, steps, output)` returns the capped text view, which goes inside `<run_data>…</run_data>` delimiters in both prompts. Any `</run_data>` in run content is replaced with `<\/run_data>` before it goes in, matched case-insensitively and allowing whitespace (`re.compile(r"<\s*/\s*run_data\s*>", re.I)`), so the data block can't be closed early.
5. **Judge:**
   - If the output kind is `none`, the judge is skipped and every criterion scores 1.
   - Otherwise the evaluator calls `complete()` with `templates/judge-v1.txt` (instructions, then the rubric JSON, then the run data block).
   - It expects a JSON object `{"criteria":[{"id","score","rationale"}]}`. The evaluator adds `max` to each entry from the rubric when it writes `scores.json`, so every entry has `id`, `score`, `max` and `rationale` (AC12). The judge is not trusted to supply `max`.
   - It validates that the response parses, that the id set equals the rubric's, that every score is an integer in [1, max], and that every rationale is non-empty.
   - On any failure (an exception or a validation error), it retries once. If the retry fails too, it records `output.score = null` and `output.error = <reason>` (AC13).
6. **Summarizer:**
   - It calls `complete()` with `templates/summarizer-v1.txt` and the same data block.
   - It checks the length by counting whitespace-separated words, and the limit is 150.
   - On failure, it retries once. If the retry fails too, the report says `Narrative unavailable: <reason>` (AC7b).
   - The Struggles section is built deterministically from the steps.
7. `report.build(...)` writes Markdown and applies `esc()` to every run-derived string:
   - `&`, `<` and `>` become HTML entities.
   - `\`, `[`, `]`, `!`, `` ` ``, `*`, `_` and `#` are backslash-escaped, so links, images, emphasis and headings show as literal text.
   - Newlines in prose fields (narrative, rationales, errors) become spaces, so run content can't start a list, heading or section.
   - Table cells also escape `|`, and newlines become spaces (AC10c).
8. The evaluator writes `rubric.json` (the raw bytes of the rubric file), `scores.json` and `report.md` into a fresh temporary directory `<run_dir>/.eval-<random>/`, then renames each into place in the order rubric, report, scores. `scores.json` goes last and carries `evaluation_id` (a random UUID), `rubric_sha256` and `report_sha256`. The report's Provenance section shows `Evaluation id: …` as visible text.
   - If a kill lands between the renames, at least one hash fails to match: either `scores.json` is the old one sitting beside a newer report or rubric, or it's the new one beside the old files.
   - The evaluator, the validator and the UI check both hashes. A mismatch is a **warning**, never a validation violation. `evaluate`'s precondition (AC10d) validates only `run.json`, `trace.jsonl` and `output.json`, so re-running `foundry evaluate` always recovers an interrupted evaluation. On a mismatch they report `Evaluation was interrupted; re-run foundry evaluate.`, and the UI card shows `incomplete`.
   - The temporary directory is removed when evaluation finishes. Stale `.eval-*` directories are ignored and cleaned up on the next evaluation.
   - After that comes the S3 mirror. It uploads only regular, non-dotfile files at the top level of the run directory, so never `.evaluating` or `.eval-*`. It runs **after** the files are renamed and **before** the lock is released.

## Data and contracts

### Additive changes to [trace-contract.md](../trace-contract.md)

These get written into the contract doc during Build.

This list is complete, and each item is a one-way door (see *Decisions*).

- **`run.json.contract_version`**: `"0.2"`.
- **`run.json.status`** gains the value `running`.
- **`run.json.limits`** is a new field: `{"max_model_calls": 20, "max_input_tokens": 600000, "max_seconds": 300}`.
- **`run.json.config`** is a new field holding the config file's path.
- **`run.json.mcp_servers[].error_detection`** is a new field.
- **Trace steps** can carry `invalid: true` on a `tool_call`. For an invalid call whose name matches no server, `server` is `null`.
- **`scores.json`** gains these fields:
  - `contract_version`: `"0.2"`
  - `evaluation_id`, `rubric_sha256`, `report_sha256`
  - `rubric_id_loaded`, alongside the existing `rubric_id`, which comes from `run.json`
  - `evaluated_at`
  - `forced`
  - `truncated_trace_lines`
  - `config`
  - `workflow.components`
  - `workflow_scorer: "v1"`
  - `output.error`
  - `output.judge`
  - `output.rubric_version`
  - `templates: {"judge": "judge-v1", "summarizer": "summarizer-v1"}`
  - `evaluation: {"tokens_in", "tokens_out", "cost_usd", "summarizer": {provider, id}}`
  - `rubric_id_mismatch`: `true` when `rubric_id_loaded` differs from `rubric_id`
  - `weights`
  - `narrative`
  - `narrative_error`

### Config (TOML)

This is `config/offline.toml`:

```toml
[task]
id = "adb"

[model]                      # the agent's model
provider = "scripted"
id = "scripted-a"
script = "tasks/adb/scripts/scripted-a/{prompt_id}.json"

[judge]
provider = "offline"
id = "offline-judge"
region = "us-west-2"         # bedrock only
max_tokens = 2048
timeout_s = 120
retries = 2

[summarizer]
provider = "offline"
id = "offline-summarizer"
region = "us-west-2"
max_tokens = 2048
timeout_s = 120
retries = 2

[limits]
max_model_calls = 20
max_seconds = 300
max_input_tokens = 600000
max_tokens = 1024
model_timeout_s = 120
model_retries = 2
mcp_start_timeout_s = 60

[mcp.servers.roda]
command = "uvx"              # resolved to an absolute path via shutil.which at run time
args = ["awslabs.roda-mcp-server@0.1.7"]
version = "0.1.7"
error_detection = "json_status"
env = {}                     # extra env; base env is an allowlist (PATH, HOME, TMPDIR, LANG, UV_*)

[tracing]
tracers = ["jsonl"]          # "jsonl" is required; "otel" optional

[tracing.otel]               # used only when "otel" is listed
endpoint = "http://localhost:6006/v1/traces"
headers_env = ""             # name of an env var holding "k=v,k2=v2" headers; value never persisted
export_timeout_s = 10
service_name = "foundry"

[storage]
s3_uri = ""                  # "s3://<bucket>" only (no key prefix; the IAM policy grants runs/* at bucket root); empty = no upload
s3_timeout_s = 30

[scoring]
workflow_weight = 0.5
output_weight = 0.5

[prices]                     # USD per million tokens
"us.anthropic.claude-sonnet-5-5" = { input = 2.20, output = 11.00 }
"us.anthropic.claude-haiku-5-5"  = { input = 0.11, output = 0.55 }
```

The other configs:

| Config | How it differs from `offline.toml` |
|---|---|
| `config/offline-alt.toml` | Only `[model]`: `id = "scripted-b"`, with scripts under `scripted-b/`. Same RODA, same prompts, a different decision sequence. This is the offline model swap (AC15). |
| `config/offline-otel.toml` | `tracers = ["jsonl", "otel"]` |
| `config/bedrock.toml` | Agent `us.anthropic.claude-sonnet-5-5`. Judge and summarizer `us.amazon.nova-2-lite-v1:0`. Region `us-west-2`. |
| `config/bedrock-alt.toml` | Only `[model] id = "us.anthropic.claude-haiku-5-5"` |

### Scripted model script

`tasks/adb/scripts/scripted-a/adb-001.json` is a JSON array of turns. Each turn is either `{"tool": "<name>", "input": {…}}` or `{"text": "<final answer>"}`. A turn may also carry an optional `"usage": {"in": n, "out": m}`. Without it, `in = ceil((len(system_prompt) + len(json.dumps(messages))) / 4)` and `out = ceil(len(json.dumps(turn)) / 4)`. If the agent asks for a model call past the end of the script, the model raises `ScriptExhausted`, which is recorded as an error `llm_call`. Fixture scripts for the limit and timeout tests live in `tests/`.

### Provider protocol

```python
class Provider(Protocol):
    def strands_model(self) -> strands.models.Model: ...        # agent use
    def complete(self, req: CompletionRequest) -> Completion: ...  # judge/summarizer use
@dataclass
class CompletionRequest: system: str; user: str; max_tokens: int; purpose: Literal["judge","summary"]; rubric: Rubric | None
@dataclass
class Completion: text: str; tokens_in: int; tokens_out: int; model_id: str
```

Providers are built **per role from that role's own config section**. The agent uses `[model]`, together with `[limits]` for `model_timeout_s`, `model_retries` and `max_tokens`. The judge uses `[judge]` and the summarizer uses `[summarizer]`, each with its own `region`, `max_tokens`, `timeout_s` and `retries`. `evaluate` never reads `[model]` or `[limits]`.

- **`bedrock`:** `complete()` uses `boto3.client("bedrock-runtime", region_name=<role region>).converse(...)` with `Config(read_timeout=<role timeout>, connect_timeout=10, retries={"max_attempts": <role retries> + 1, "mode": "standard"})`. `strands_model()` returns `BedrockModel(model_id=…, region_name=…, boto_client_config=<same Config>, max_tokens=…)`.
- **`offline`:** `complete()` returns real JSON text for judge requests, built from `req.rubric` with every criterion at `max − 1`, rationale `Offline judge: fixed score for pipeline testing.`. For summary requests it returns a fixed 2-sentence narrative that names the step count, taken from `req.user`. Its responses still go through the real parser and validator.
- **`scripted`:** `complete()` replays `{"text": …}` turns from its script. Tests use it for malformed-judge cases (AC13).

### Rubric and prompts

- `tasks/adb/rubric.json` is the stand-in rubric `adb-rubric-v0`. It has four criteria, each with `max` 4: `data-fit` (does the plan pick appropriate open datasets), `feasibility` (access, format, region and cost noted, including Requester-Pays), `method` (concrete, ordered build steps), and `limitations` (states gaps and risks).
- `tasks/adb/prompts.jsonl` holds 5 synthetic ADB-style prompts, `adb-001` to `adb-005`, from easy to hard. A file `tasks/adb/system.txt` holds the agent's system prompt.

## Decisions

### One-way doors

These are public contracts. Once Clay's harness or data depends on them, they are costly to change, so they are flagged as **one-way**:

- the trace-contract field names and meanings, including every additive field in this design
- the run id format `<UTC ts>_<prompt_id>_<4 hex>`, which is part of the S3 key
- the S3 key layout `runs/<run_id>/<file>`
- the `scores.json` shape

Mitigations:

- `run.json` and `scores.json` carry `contract_version: "0.2"`, where the original draft is 0.1. Readers branch on that field.
- Rule 1 still applies: changes stay additive. Removing or renaming a field needs a version bump and the whole team's agreement.
- Consumers must not parse the run id. It is an opaque key.

### ADR-1: Strands Agents as the harness, behind our own provider and tracer seams

- **Context:** M1 needs a Bedrock agent with MCP tools. M6 needs a swappable model. Clay's harness might replace ours.
- **Options:**
  - A) Strands Agents
  - B) a hand-rolled Bedrock Converse loop plus the `mcp` SDK
  - C) LangGraph
- **Decision:** A.
- **Why:** It is AWS-native and has a first-class MCP client. Its hooks expose everything the contract needs (tool name, input, result, exception and duration; per-model-call usage; cancellation). The spike ran a custom scripted `Model` subclass against real RODA and captured exactly that. Our `Provider` and `Tracer` seams keep Strands from leaking into evaluation, which reads only files.
- **Reversibility:** easy. Only `harness/` and `models/*.strands_model` touch Strands.
- **Sources:**
  - strands-agents 1.62.0 source (`strands/models/model.py` `Model.stream`; `strands/hooks/events.py`; `Agent.cancel` docstring; `agent.py:342,557`: `retry_strategy` defaults to 6 attempts, and `None` means `max_attempts=1`), read 2026-10-29
  - strands 1.62.0 `models/bedrock.py:1856-1861`: BedrockModel puts the Converse `usage` into the `metadata` stream event, the same place the spike read it from with the scripted model. Live confirmation comes in L1.
  - spike `scratchpad/spike/spike.py` run 2026-10-29: scripted model + RODA 0.1.7, hooks fired for 3 tools including an unknown tool (`selected_tool=None`, `exception=Unknown tool`), and per-call usage was in `stop_response.message["metadata"]["usage"]`

### ADR-2: Trace capture through our own hooks, with OTel spans emitted by our tracer rather than Strands's built-in telemetry

- **Context:** M6 requires a swappable tracer. The JSONL file is the system of record, and a second backend must show the same run.
- **Options:**
  - A) use `StrandsTelemetry` for OTel and our hooks for JSONL
  - B) our hooks feed one Step stream into N tracers, one of which emits OTel spans
- **Decision:** B.
- **Why:** Both tracers see identical steps, with the same fields, the same retry and invalid logic, and the same RODA error detection. Tracer swap is a list in config. The OTel output doesn't depend on Strands's span layout, so a non-Strands harness gets the same OTel output. Strands's own telemetry stays off: we never create `StrandsTelemetry`, so its global provider stays a no-op.
- **Span layout:**
  - one root span, `invoke_agent foundry`
  - one child span per step, with explicit start and end times from the step: `chat <model>` for llm calls, `execute_tool <name>` for tool calls
  - attributes `gen_ai.operation.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.tool.name`, plus `foundry.run_id`, `foundry.step`, `foundry.cost_usd`, `foundry.retry_of` and `foundry.invalid`
  - error status on errored steps
  - exporter: `OTLPSpanExporter(endpoint, headers, timeout)` behind a `BatchSpanProcessor`, on a **private `TracerProvider`** owned by the tracer. We never call `trace.set_tracer_provider`, so Strands' built-in instrumentation, which uses the global provider, stays a no-op and can't emit through our exporter.
  - `end_run` runs `force_flush` and `shutdown` in a daemon thread and joins it with `timeout = export_timeout_s`, so the extra time is bounded by our own join and doesn't depend on the exporter's internal retry or backoff behavior.
  - a wrapper exporter catches any failure or `FAILURE` result and prints the AC16b warning once. The warning is also printed if the join times out.
  - The S3 mirror is bounded the same way: botocore `connect_timeout = read_timeout = s3_timeout_s` and `max_attempts = 2`.
- **Reversibility:** easy.
- **Sources:**
  - OTel GenAI semconv v1.44.0. The attributes exist in `opentelemetry.semconv._incubating.attributes.gen_ai_attributes` (Development status) in `opentelemetry-semantic-conventions` 0.66b2.
  - Phoenix 20.27.0 accepted OTLP/HTTP at `/v1/traces` locally with no account.
  - All verified 2026-10-29.
- **Known risk:** the GenAI attributes are still experimental and may be renamed. They're pinned through the semconv package version.

### ADR-3: Offline mode fakes only the models. MCP is the real RODA.

- **Context:** There are no AWS credentials. RODA needs none, which was verified by running it with AWS config set to `/dev/null`. The user asked to call the real RODA.
- **Decision:** `config/offline.toml` uses the real RODA 0.1.7 through `uvx`. A test-only fault server (`tests/mcp_fault_server.py`, built on `mcp.server.mcpserver.MCPServer`, which is what `FastMCP` became in mcp 2.x; see the `mcp/server/fastmcp.py` stub in mcp 2.1.1) gives the timeout, error and failed-start cases. Tests that need RODA are marked `network`.
- **Why:** Offline runs then show real tool traffic and real RODA quirks, such as errors reported inside a successful result.
- **Trade-off accepted:** the happy-path tests depend on network access and on the registry staying up. Tests assert structure, never RODA content.
- **Reversibility:** easy.
- **Sources:** grounding run 2026-10-29: `roda.py` with `AWS_CONFIG_FILE=/dev/null`, all tools returning `is_error=False`. The server reaches the registry over HTTPS and uses unsigned S3 (`aws_client.py`).

### ADR-4: Files plus S3 mirror. No database.

- **Decision:** Run directories on disk are the store, mirrored to S3 when configured. The UI scans `runs/*/run.json` on each request.
- **Why:** This is what the contract and the spec non-goals say. The data is small: a scan of 1,000 runs reads 1,000 small files, which is fine locally.
- **Reversibility:** easy. The files load into Athena or Snowflake later.

### ADR-5: Server-rendered UI (FastAPI + Jinja2), no JS build. Runs are started by calling the CLI as a subprocess.

- **Options:**
  - A) FastAPI + Jinja2 HTML
  - B) a React SPA + FastAPI JSON
- **Decision:** A.
- **Why:**
  - It's one language and one process, and Node is only needed for CDK.
  - With no client JavaScript there's less XSS surface.
  - Starting runs with `subprocess.Popen([sys.executable, "-m", "foundry", "run", …])` means the UI goes through exactly the CLI path, and a crash in a run can't take down the server.
- **Trade-off:** the Technical doc lists TypeScript for the thin UI, but it's a "should", and Python keeps the team on one stack. This departure is recorded.
- **Reversibility:** easy.
- **Sources:** FastAPI and Starlette docs (`TrustedHostMiddleware`). The versions get pinned at Build and their APIs checked against the installed versions.

### ADR-6: CDK in Python inside the repo, with synth tested through `aws_cdk.assertions`

- **Decision:**
  - `infra/` holds `app.py` and `stack.py`.
  - The Python dependencies `aws-cdk-lib==2.275.0` and `constructs` sit in the default `dev` dependency group, so plain `uv run pytest` installs them.
  - The CDK CLI is pinned in `infra/package.json` as `aws-cdk@2.1146.1` and run through `npx`.
  - pytest asserts the template properties with `Template.from_stack`, so it needs no CLI and no Docker.
  - Verify also runs `npx cdk synth` once.
- **Stack:**
  - region `us-west-2` (`env=Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region="us-west-2")`)
  - bucket `RunsBucket` with `block_public_access=BLOCK_ALL`, `encryption=S3_MANAGED`, `versioned=True`, `enforce_ssl=True` and `removal_policy=RETAIN`
  - role `HarnessRole` assumable by the account root, with a policy allowing exactly `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` (no wildcards) on `*`, plus `s3:GetObject`, `s3:PutObject` and `s3:ListBucket` (with a `runs/*` prefix condition) on the bucket
  - tags `project=clay-foundry`, `owner=team-46`
- **Reversibility:** easy for synth. The bucket is a retained stateful resource on deploy, but deploying is L4 and not part of this build.
- **Sources:**
  - grounding 2026-10-29: `cdk synth` with CLI 2.1146.1 and lib 2.275.0 working with no Docker (`dockerImages: {}`)
  - AWS Bedrock user guide, "Inference using Converse API", accessed 2026-10-29: "To call `Converse`, you require permission for the `bedrock:InvokeModel` operation. To call `ConverseStream`, you require permission for the `bedrock:InvokeModelWithResponseStream` operation."
- **Reversibility of the bucket:** the bucket is a retained stateful resource, so deleting it on deploy is a **one-way** action, and `RETAIN` prevents that.

### ADR-7: Default live models

- **Decision:**
  - Agent: `us.anthropic.claude-sonnet-5-5`.
  - Alternative agent: `us.anthropic.claude-haiku-5-5`.
  - Judge and summarizer: `us.amazon.nova-2-lite-v1:0`. It's a different vendor from the agent, so the agent never grades itself.
- **Why:** These are verified current IDs that are available from us-west-2 as geo cross-region profiles.
- **Risk:** Nova 2 Lite's end-of-life is "no sooner than Dec 02, 2026". The fallback judge is whichever of Sonnet 5.5 and Haiku 5.5 isn't the agent model for that config, which is a config change. The evaluator warns if the judge equals the agent.
- **Reversibility:** easy.
- **Sources:**
  - Bedrock model cards for Sonnet 5.5, Haiku 5.5 and Nova 2 Lite, accessed 2026-10-29
  - Anthropic pricing page (platform.claude.com/docs/en/about-claude/pricing), accessed 2026-10-29: Sonnet 5.5 $2/$10 per MTok, Haiku 5.5 $0.10/$0.50, plus 10% for regional/geo endpoints on Bedrock
  - Bedrock account model access is unverified (no credentials). See Risks.
- **Unverified assumption (owner: builder, checked at L1):** Bedrock's Claude prices equal the first-party prices plus the documented 10% premium for regional and geo endpoints. The Bedrock pricing page renders its tables client-side, so it couldn't be read. Nova 2 Lite's price is unknown, so `bedrock.toml` has **no** `[prices]` entry for it, and evaluation `cost_usd` is `null` under that config until the price is filled in, which AC7 allows. The $2 ceiling check uses Sonnet prices as an upper bound for the judge.

### ADR-8: Live models changed to Sonnet 4.6 and Haiku 4.5 because of account quotas (2026-10-30)

- **Context:** `protagona-sandbox-1` (283171504459), us-west-2. The applied Service Quotas for cross-region inference on Claude Sonnet 5.5 and Haiku 5.5 are **0 tokens per minute**, and a Converse call to `us.anthropic.claude-sonnet-5-5` returned `AccessDeniedException`. Usable quotas: Sonnet 4.6 at 6M tokens/minute and 10k requests/minute, Haiku 4.5 at 5M and 10k, Nova 2 Lite at 8M and 2k. A one-token Converse call to each succeeded.
- **Options:**
  - A) request a quota increase for the 5.5 models and wait
  - B) use the models the account can call now
- **Decision:** B.
  - Agent: `us.anthropic.claude-sonnet-4-6`.
  - Alternative agent: `us.anthropic.claude-haiku-4-5-20251001-v1:0`.
  - Judge and summarizer stay on `us.amazon.nova-2-lite-v1:0`, a different vendor.
  - Live `max_input_tokens` is lowered to 350,000, which keeps the worst case at or below $2.00 per run at Sonnet 4.6 prices.
- **Why:** it unblocks the live checks today. The model is a config value (M6), so moving back to 5.5 later is a one-line change once a quota is granted.
- **Reversibility:** easy.
- **Sources:**
  - `aws service-quotas list-service-quotas --service-code bedrock --region us-west-2` and `aws bedrock-runtime converse`, run 2026-10-30
  - Anthropic pricing page, accessed 2026-10-29: Sonnet 4.6 $3/$15 per million tokens, Haiku 4.5 $1/$5; plus the 10% regional/geo premium on Bedrock

### ADR-9: Kimi K3 as a third, non-Anthropic agent; model capability decided by probing, not quotas (2026-10-30)

- **Context:**
  - The user asked for Kimi K3 in the model mix and for a real inventory of usable models.
  - Kimi K3 failed its first live run with `ValidationException: This model doesn't support the temperature field`.
  - An earlier quota-based answer had listed models that turn out to be access-denied (OpenAI GPT-5.x/6.x) or that can't do streaming tool use, which is how Strands calls Bedrock (Meta Llama).
- **Decision:**
  - Add `config/bedrock-kimi.toml`, with agent `us.moonshotai.kimi-k3` and **no `temperature`** line. The Nova 2 Lite judge is unchanged.
  - Decide model capability only from `scripts/bedrock_model_sweep.py`, which calls each model with the harness's exact features. Results are in [bedrock-models.md](bedrock-models.md).
- **Why:** `temperature` is optional in `ModelCfg`, so omitting it needs no code change. Probing is the only source that reflects this account's real access.
- **Reversibility:** easy.
- **Sources:**
  - live ConverseStream calls on 2026-10-30, where streaming, system prompt and tools succeeded and `temperature` was rejected
  - two sweep runs on 2026-10-30
  - Kimi K3 live run: succeeded, 9 RODA calls, overall 1.00 under the Nova judge

### ADR-10: Judge replaced: Nova 2 Lite → Kimi K3 (GLM 5 when the agent is Kimi), chosen by calibration (2026-10-30)

- **Context:** the user distrusted Nova 2 Lite as a judge. On a 5-case calibration set it gave a vague one-paragraph plan 0.75 and ranked it above a real 297-word plan (0.67).
- **Options:**
  - Kimi K3, GLM 5 and Opus 4.6 all separated the cases correctly and caught the fabricated dataset by citing the trace.
  - Mistral Large 3 was fooled by the fabrication.
  - DeepSeek V3.2 was inconsistent between repeats.
  - Sonnet 4.6 was good, but it graded its own runs.
- **Decision:**
  - The judge is `us.moonshotai.kimi-k3`: consistent across repeats, and a different vendor from the default Claude agents.
  - With a Kimi agent (`config/bedrock-kimi.toml`) the judge is `zai.glm-5`.
  - The summarizer is unchanged (Nova 2 Lite).
- **Trade-offs:**
  - Kimi K3 has no price in `[prices]`, so judge cost is recorded as null.
  - Opus 4.6 was slightly sharper, but it is the same vendor as the Claude agents and the most expensive option ($5/$25 per million tokens).
- **Reversibility:** easy (config).
- **Sources:** the calibration runs of 2026-10-30, recorded in [bedrock-models.md](bedrock-models.md#judge-calibration-2026-10-30).

### ADR-11: Assign roles by price: the cheaper model takes the token-heavy role (2026-10-30)

- **Context:**
  - Measured per run, the agent uses about 48k input and 1.9k output tokens (median of 7 successful runs; up to 93k input). The judge uses about 3.3k input and 0.5–2k output.
  - The agent is therefore the heavy role, about 15× the judge's input.
  - The user asked to put the cheaper of Kimi K3 and Opus 4.6 on the heavy role and the more expensive one on the other.
- **Prices:** AWS Price List API, us-west-2, `us.` geo profile, standard tier, queried 2026-10-30:

  | Model | Input | Output | Cache read |
  |---|---|---|---|
  | Kimi K3 | $3.30 | $16.50 | $0.33 |
  | Opus 4.6 | $5.50 | $27.50 | $0.55 |

  Kimi K3 is 40% cheaper on every token type, and it caches prompts automatically.
- **Decision:** `config/bedrock-kimi.toml` uses **Kimi K3 as the agent and Opus 4.6 as the judge**. Opus was also the sharpest judge in the calibration (ADR-10), and it isn't the agent's vendor. The Nova 2 Lite summarizer is unchanged.
- **Measured** (adb-001, 2026-10-30):

  | Role | Model | Calls | Input | Output | Cost |
  |---|---|---|---|---|---|
  | Agent | Kimi K3 | 4 | 39,697 (24,290 cached) | 4,284 | $0.130 |
  | Judge | Opus 4.6 | 1 | 3,043 | 483 | $0.030 |
  | Summarizer | Nova 2 Lite | 1 | 1,955 | 104 | $0.001 |
  | **Total** | | | | | **$0.16** (overall score 0.96) |

  The reverse assignment, Opus as agent and Kimi as judge, priced on the same token counts: about $0.34 for the agent (Claude doesn't cache unless asked) plus about $0.04 for Kimi as judge, which writes about 1.9k output tokens. That's about **$0.38, 2.4× more**.
- **Also changed:**
  - Every live config now carries the official prices, which corrects Nova 2 Lite: the configured $3.30/$16.50 "upper bound" was about 10× its real $0.33/$2.75.
  - Prices take an optional `cache_read` rate, so cached input is priced at the cache rate.
- **Worst case per run** (Kimi agent, Opus judge): $0.66 + $0.81 for the agent, ≤ $0.33 for the judge (2 calls), ≤ $0.02 for the summarizer. That's **≤ $1.83**.
- **Reversibility:** easy (config).

### ADR-12: Kimi K3 agent + Opus 4.6 judge is the default live config (2026-10-30)

- **Decision:**
  - `config/bedrock.toml` is now ADR-11's setup: Kimi K3 agent, Opus 4.6 judge, Nova 2 Lite summarizer. The user approved it ("I like it").
  - `config/bedrock-alt.toml` is identical except the agent is Claude Sonnet 4.6. That keeps the model-swap demo a pure `[model]` change, and a test enforces it.
  - `config/bedrock-kimi.toml` is removed, because it duplicated the new default.
  - The Haiku 4.5 agent config is retired; Haiku is still listed as agent-capable in `bedrock-models.md`.
- **Reversibility:** easy.

## Failure modes

| Failure | Detection | Behavior | Recovery |
|---|---|---|---|
| MCP server doesn't start or handshake | `MCPClientInitializationError` within `mcp_start_timeout_s` | `note` step, run `failed`, exit 1 | Fix the command or network and rerun |
| `uvx` not on PATH | `shutil.which` is None | Treated as a failed MCP start (AC9c): `note` step `MCP server 'roda' failed to start: command 'uvx' not found on PATH. Install uv (see README).`, run `failed`, exit 1 | Install uv |
| Tool error (MCP `isError` or RODA JSON status) | Hook | Errored step; the model sees the error | The agent may retry, which links `retry_of` |
| Unknown tool or bad arguments | Hook (`selected_tool` None or schema failure) | Errored step marked `invalid`; the model gets an error result | — |
| Model throttled or erroring | botocore retries, then `AfterModelCallEvent.exception` | Errored `llm_call`, run `failed`, exit 1 | Rerun later |
| Agent loops on tools | Model-call counter | Next call cancelled, `note`, `failed` | Raise `max_model_calls` or fix the prompt |
| Run hangs | Watchdog at `max_seconds` | `agent.cancel()`, `timeout`. A hard stop at +3 s. | — |
| Judge malformed or raising | Parser or validator | One retry, then `output.score = null` and overall `null` | Re-evaluate later |
| Summarizer fails or runs long | Validator | One retry, then `Narrative unavailable` | — |
| OTLP endpoint down | Exporter result or exception | One warning; JSONL is unaffected | Start the backend and rerun |
| S3 upload fails | Exception | One warning; exit code unchanged | Re-evaluate to upload again |
| Process killed mid-run | `run.json` stays `running` | UI shows `running`; evaluate refuses without `--force` | `foundry evaluate --force` |
| Process killed mid-evaluate | A hash mismatch in `scores.json`, or a leftover `.eval-*` directory or `.evaluating` lock | Shown as `incomplete` with `Evaluation was interrupted` | Re-evaluate. Stale `.eval-*` directories are removed and a stale lock is replaced. |

## Security and privacy

### Trust boundaries

1. **Run content** (model text, tool results, agent output) is untrusted. It reaches four places:
   - **Judge and summarizer prompts:** inside the `<run_data>` block, with any closing tag inside it neutralized (AC10b)
   - **`report.md`:** escaped (AC10c)
   - **UI HTML:** Jinja2 autoescape for fields, and markdown-it with `html=False`, the image rule disabled and `validateLink` allowing only `http:`, `https:` and relative links (AC27)
   - **The agent's own context:** capped at 8,000 characters per tool result (a non-goal mitigation)
2. **The UI's HTTP surface** on localhost:
   - `TrustedHostMiddleware(allowed_hosts=["127.0.0.1", "localhost"])`, plus a port check in our own middleware, gives 400 otherwise
   - a POST whose `Origin` is present and isn't the server's origin gets 403
   - a POST without a CSRF token matching the per-process `secrets.token_urlsafe(32)`, compared with `hmac.compare_digest`, gets 403
   - `run_id` must match `^[A-Za-z0-9._-]+$` and not contain `..`, else 404
   - the resolved path must sit under `runs_dir`, checked with `Path.resolve().is_relative_to`
   - form `config` must match a stem in `config/`, and `prompt_id` must be in that task's prompts (400 otherwise)
   - at most one UI-started run (409)
   - the subprocess is started with an argument list, never a shell
3. **The MCP subprocess environment** is built from an allowlist (`PATH`, `HOME`, `TMPDIR`, `LANG`, `UV_*`, plus `env` from config). It never includes `AWS_*`, because RODA doesn't need credentials.

### Secrets

- None are stored anywhere.
- OTLP headers come from an environment variable named in config. The value is read at export time and never written or logged.
- `run.json` records only the endpoint.
- Error strings from botocore don't include credentials.
- AC21 is the test.

### Dependencies

These are pinned in `uv.lock`, and Verify runs `pip-audit` (or `osv-scanner`) and `gitleaks`. They are all Apache-2.0, MIT or BSD licensed.

- `strands-agents`
- `mcp`
- `boto3`
- `fastapi`
- `uvicorn`
- `jinja2`
- `python-multipart`
- `markdown-it-py`
- `pydantic`
- `jsonschema`
- `opentelemetry-sdk`
- `opentelemetry-exporter-otlp-proto-http`
- `opentelemetry-semantic-conventions`

### Personal data

None. The only data is synthetic prompts and public datasets.

### Threat model (STRIDE)

| Entry point | Threats | Mitigation |
|---|---|---|
| UI GET pages | Information disclosure through path traversal; XSS from run content | `run_id` regex and resolve check; escaping |
| UI POST `/runs` and `/runs/<id>/evaluate` | CSRF or DNS rebinding triggering paid runs; denial of service through many runs | Host, Origin and CSRF checks; one run at a time |
| Judge prompt | Tampering through prompt injection in the output | Data block with closing-tag neutralization; L5 |
| MCP subprocess | Credential exfiltration by a third-party server | Environment allowlist with no AWS variables |

Attack surface: the UI is bound to localhost and isn't exposed publicly, so it's out of scope for the next external pentest. This design records it here, and `docs/security/` gets bootstrapped in Verify.

## Performance

There is no latency SLO. Budgets:

- `foundry evaluate` on a fixture, offline, takes under 2 s.
- The UI index with 200 runs renders in under 500 ms. Both are measured in Verify.

**Cost budget, worst case per live run** (from ADR-7 prices with the +10% geo premium):

| Item | Assumption | Cost |
|---|---|---|
| Agent input | A hard cap of `max_input_tokens` = 600,000 per run, enforced before each call using Strands' `projected_input_tokens`. The cap holds however many tool calls happen per turn. | ≤ 600k × $2.20/M = $1.32 |
| Agent output | 20 calls × `max_tokens` 1,024 | ≤ 20.5k × $11/M = $0.23 |
| Evaluation | 4 calls (judge and summarizer, one retry each). Each has a capped view of about 20k input tokens (20k characters of output ≈ 5k tokens, plus 200 timeline lines × about 250 characters ≈ 12.5k tokens, plus instructions and the rubric) and 2,048 output tokens. Priced at Sonnet rates as an upper bound, because Nova 2 Lite's price couldn't be read from the AWS pricing page (it renders client-side). | ≤ 80k × $2.20/M + 8.2k × $11/M = $0.27 |
| **Total** | | **≤ $1.82**, under the $2.00 ceiling |

Without the input cap, 20 calls whose context grows by about 3k tokens per turn (about 1k of output plus about 2k of tool result) would reach about 600k input tokens on average. With several tool calls per turn it could go higher, which is why the cap exists.

A typical run of about 6 calls is about $0.15. At 10× usage (100 runs per day), the cost is about $15–175 per day. The budget alarm is left as a follow-up for L4 deploy.

## Migration and rollback

This is greenfield, so there's no migration. Rollback means reverting commits on the `prototype` branch. Run directories are separate from the code and are git-ignored.

## Traceability

Every test named here exists and passes (115 tests, also passing in a fresh clone, 2026-10-30).

| AC | Component(s) | Test(s) |
|---|---|---|
| AC1 | cli, config, tasks | `test_cli.py::test_unknown_prompt_exits_2_without_run_dir`, `::test_missing_config_exits_2`, `::test_invalid_config_names_key`, `::test_bad_prompt_id_line_is_config_error` |
| AC2 | cli | `test_cli.py::test_exit_1_for_failed_run`, `::test_exit_0_for_succeeded_run_with_fault_server`, `::test_unwritable_runs_dir_exits_2_with_message`, `test_evaluate.py::test_replay_cli_does_not_run_agent` |
| AC3 | contract | `test_contract.py` (all) |
| AC4 | runstore, runner | `test_runner.py::test_run_json_running_during_run`, `::test_happy_path_against_fault_echo`, `test_evaluate.py::test_reevaluate_keeps_run_files` |
| AC5 | runner, mcp, hooks, jsonl | `test_cli.py::test_run_offline_real_roda` (network) |
| AC6 | hooks | `test_runner.py::test_tool_error_and_retry`, `test_cli.py::test_roda_json_status_error_detected` (network), `test_runner.py::test_json_status_error_detection` |
| AC7 | hooks | `test_runner.py::test_tokens_and_cost` |
| AC8 | hooks | `test_runner.py::test_max_model_calls`, `::test_max_input_tokens` |
| AC9 | runner | `test_runner.py::test_timeout_records_inflight_tool` |
| AC9b | hooks, runner, bedrock | `test_runner.py::test_model_failure`, `test_bedrock.py::test_throttling_becomes_provider_error` |
| AC9c | mcp, runner | `test_runner.py::test_mcp_server_fails_to_start`, `::test_mcp_command_not_found` |
| AC9d | hooks | `test_runner.py::test_unknown_tool_is_invalid`, `::test_schema_invalid_args` |
| AC10, AC7b | summarize, report | `test_evaluate.py::test_report_sections`, `::test_struggles_list_everything`, `::test_narrative_failure` |
| AC10b | view, judge | `test_evaluate.py::test_injection_only_inside_data_block`, `::test_view_caps` |
| AC10c | report | `test_report.py::test_hostile_fixture_escaped`, `::test_esc_neutralizes_markdown_and_html` |
| AC10d | evaluate, lock | `test_evaluate.py::test_refuses_running_and_force`, `::test_invalid_run_dir_refused`, `::test_concurrent_evaluation_refused`, `::test_stale_lock_replaced`, `::test_interrupted_evaluation_recovers` |
| AC10e | config, evaluate | `test_evaluate.py::test_eval_config_errors`, `::test_eval_only_config_accepted_and_recorded` |
| AC11 | workflow | `test_workflow.py` (all) |
| AC12 | judge | `test_evaluate.py::test_judge_criteria_and_identity`, `::test_none_output_skips_judge` |
| AC13 | judge | `test_evaluate.py::test_judge_malformed_twice`, `::test_judge_raises_then_recovers_and_raises_twice` |
| AC14 | report | `test_report.py::test_provenance`, `test_evaluate.py::test_eval_only_config_accepted_and_recorded` |
| AC15 | config, runner | `test_cli.py::test_model_swap_no_code_change` (network), `test_bedrock.py::test_bedrock_configs_differ_only_in_model_id` |
| AC16, AC16b | otel | `test_otel.py` (all) |
| AC17, AC18 | evaluate | `test_evaluate.py::test_replay_cli_does_not_run_agent`, `::test_replay_never_touches_runner`, `::test_reevaluate_keeps_run_files` |
| AC19 | evaluate | `test_evaluate.py::test_rubric_snapshot_and_override`, `::test_invalid_rubric_rejected_before_any_model_call` |
| AC20 | storage | `test_storage.py` (all) |
| AC21 | all | `test_secrets.py::test_sentinel_absent_everywhere`, `test_ui.py::test_ui_pages_never_show_credentials` |
| AC22 | infra | `test_infra.py` (all) and `npx cdk synth` |
| AC23 | repo | `test_repo.py` |
| AC24–AC29 | ui | `test_ui.py` (all) |
| AC30 | all | full suite and `-m "not network"`, in the working tree and in a fresh clone |
| AC31 | README | zero-context walkthrough in Verify |

## Risks and spikes

- **Riskiest assumption: Strands hooks are enough for the trace.** Settled by the spike (ADR-1).
- **Resolved in slice 3 (observed 2026-10-30):** `agent.cancel()` during an in-flight MCP call fires `AfterToolCallEvent` with `exception` set to "Tool execution cancelled locally; remote execution may have continued", not with `cancel_message`. TraceHooks records any errored or cancelled tool call that finishes after the watchdog fired as `cancelled: run timed out`. MCP results also carry `structuredContent`, so the 8,000-character cap rebuilds the result instead of patching `content`. *Original note:* watchdog cancel during an in-flight MCP call produces an `AfterToolCallEvent` we can mark as cancelled. Strands documents cancellation during MCP tool execution, but the event's exact shape on cancel hasn't been observed yet. It's checked in Build slice 3 with the fault server's sleep tool. Fallback: the runner records the in-flight tool from `BeforeToolCallEvent` state when no After event arrives.
- **Unverified: Bedrock model access in the target account.** It's checked in L1. Fallback: switch model ids in config.
- **Network-dependent tests:** RODA or PyPI outages make `network` tests fail. CI can run `-m "not network"`.

## Implementation plan

<!-- ship-it: before dispatching the plan reviewer, extract this section and pass it alongside spec.md only. -->

All work stacks on the `prototype` branch. Each slice ends with `uv run pytest` green and a commit that names the slice and its ACs, e.g. `feat(harness): agent run with RODA + trace hooks (AC1-AC9d)`. The run commands assume the repo root is the current directory, which is the project root used for path resolution (`$FOUNDRY_HOME` overrides it). Pinned versions:

- strands-agents 1.62.0
- mcp 2.1.1
- aws-cdk-lib 2.275.0
- aws-cdk CLI 2.1146.1
- `opentelemetry-semantic-conventions` 0.66b2

The other dependencies are pinned at their latest release when `uv lock` runs in slice 1. Before every commit, `gitleaks` runs on the staged diff.

**Normative inputs.** The plan is executed alongside this design document. These sections are the specification for the slices that consume them:

- *Data and contracts*: the config TOML schema, the scripted script format and token formula, the `scores.json` fields, and the provider protocol
- *One run* and *One evaluation*: thread model, finalize order, status rules, the lock file format, and atomic writes
- *Security and privacy*: the dependency list and trust boundaries
- *Experience*: the UI states and the approved wireframe

Additive contract fields go into `docs/trace-contract.md` in the slice that first writes them. `gitleaks` 8.30.1, installed locally through Homebrew, scans each commit's staged diff. If a slice 3 spike shows a Strands behavior differs from the design, the design is updated first. A fallback to a hand-rolled Converse loop would change only `harness/` and `Provider.strands_model()`, while `Tracer`, `Step` and evaluation stay unchanged.

**Shared interfaces.** These names are used across slices. Each is defined in the slice shown in brackets.

- `foundry.contract`: `RunRecord`, `Step`, `Output`, `Scores`, `Rubric` (pydantic models). `validate_run_dir(path) -> list[Violation]`, where `Violation` has `file`, `line`, `field`, `message` and `severity: "error"|"warning"`. [2]
- `foundry.runstore`: `new_run_id(prompt_id) -> str`, `RunDir(path)` with `write_run(RunRecord)`, `append_step(Step)`, `write_output(Output)`, `read_run()`, `read_steps()`, `read_output()`, and `atomic_write_many({name: bytes})`. [2]
- `foundry.config`: `load_config(path) -> Config` for `run`, which validates every section. `load_eval_config(path) -> EvalConfig` for `evaluate`, which reads and validates only `[judge]`, `[summarizer]`, `[scoring]`, `[prices]` and `[storage]` (AC10e), so a config file used only for evaluation is valid. Both raise `ConfigError(file, key, message)`. Also `project_root() -> Path`. [3]
- `foundry.models.base`: `Provider` protocol with `strands_model()` and `complete(CompletionRequest) -> Completion`. `build_provider(role_cfg) -> Provider`. [3; the bedrock provider comes in 6]
- `foundry.tracing.base`: `Tracer` protocol with `start_run(RunRecord)`, `record_step(Step)`, `end_run(RunRecord)` and `close()`. `build_tracers(cfg, run_dir) -> list[Tracer]`. [3; otel comes in 5]
- `foundry.harness.runner`: `run(cfg, prompt_id, runs_dir, run_id=None) -> (Path, status)`. [3]
- `foundry.evaluate.evaluate`: `evaluate(run_dir, cfg, rubric_path=None, force=False, lock_token=None) -> Scores`. [4]
- `foundry.storage`: `mirror(run_dir, storage_cfg)`, called by the runner after finalize and by evaluate after the renames. [5; until then it is a no-op function, created in 3]

**Slices:**

1. **Skeleton (AC23).**
   - `pyproject.toml`: uv, `requires-python = ">=3.12"`, `license = "Apache-2.0"`, console script `foundry = "foundry.cli:main"`, runtime dependencies listed in *Security and privacy*. A default `dev` group with pytest, httpx, aws-cdk-lib, constructs and moto-free botocore stubs. A `[tool.pytest.ini_options]` with a `network` marker.
   - `uv.lock`.
   - `LICENSE`: Apache-2.0 text.
   - `.gitignore`: `runs/`, `.venv/`, `cdk.out/`, `node_modules/`, `__pycache__/`.
   - `src/foundry/__init__.py` and `__main__.py`.
   - `cli.py` with argparse subcommands `run`, `evaluate`, `validate` and `ui`, each exiting 2 with `not implemented` for now.
   - `tests/test_repo.py`, asserting LICENSE and the pyproject license.
   - `.github/workflows/ci.yml`: checkout; asdf or setup actions for uv and Node from `.tool-versions`; `uv sync`; `uv run pytest`.
   - Commit.
2. **Contract, validator, fixtures (AC3, AC4 models).**
   - `contract.py`:
     - models for every field in trace-contract.md, plus the additive fields in *Data and contracts*
     - the rubric model with the spec's rules
     - `validate_run_dir`, which parses `trace.jsonl` line by line so it can report line numbers, and treats truncated-last-line and hash-mismatch as warnings
   - `runstore.py`.
   - Hand-write `fixtures/runs/{clean,recovered,failed,injection,hostile}/`, each with `run.json`, `trace.jsonl` and `output.json`, and no scores.
     - `recovered` has a RODA error followed by a `retry_of` success.
     - `failed` ends `failed` with `kind: none`.
     - `injection` and `hostile` carry the AC10b and AC10c payloads.
   - `foundry validate` wired to `validate_run_dir` (exit 0/1, one line per violation).
   - Update `docs/trace-contract.md` with the additive fields and `contract_version`.
   - Tests: fixtures valid; seeded violations reported with file:line; truncated last line is a warning only when the status is `running`.
   - Commit.
3. **Harness, the riskiest slice (AC1, AC2, AC4–AC9d, AC15).**
   - `config.py`, covering every section in the design's TOML example: validation, `ConfigError` messages, the requirement that `jsonl` is in `tracers`, `s3_uri` holding only a bucket, and scoring weights summing to 1.
   - `models/base.py` holds the `Provider` protocol and `CompletionRequest`/`Completion`. `models/registry.py` holds `build_provider`.
   - `models/scripted.py`: `ScriptedModel(strands.models.Model)` replays the turns of the script format in *Data and contracts* → "Scripted model script". It emits the Converse-style stream events that the pre-Build spike (ADR-1) confirmed Strands accepts: `messageStart`, `contentBlockStart`/`contentBlockDelta` (toolUse input as a JSON string, or text), `contentBlockStop`, `messageStop` with `stopReason` `tool_use` or `end_turn`, then `metadata.usage`. applies the token formula, raises `ScriptExhausted` past the end, and its `complete()` replays text turns.
   - `models/offline.py` and `models/registry.py`.
   - `tasks/adb/`: `prompts.jsonl` (5 prompts), `rubric.json`, `system.txt`, and scripts `scripted-a/adb-00{1..5}.json` and `scripted-b/…` using real RODA tool names.
   - `harness/mcp.py`: resolves the absolute command with `shutil.which`, building the environment from an allowlist.
   - `harness/hooks.py`, `tracing/base.py` and `tracing/jsonl.py`.
   - `harness/runner.py`, with the worker thread, watchdog, finalize order and status rules from *One run*.
   - `storage.mirror` as a stub.
   - `config/offline.toml` and `config/offline-alt.toml`. They differ only in `[model]`, whose `id` and `script` keys select `scripted-a` or `scripted-b`.
   - `tests/mcp_fault_server.py` (FastMCP), with tools `fail(msg)`, `sleep(seconds)` and `echo(text)`, and a `--crash-on-start` flag.
   - Hooks also cap each tool result passed back to the agent at 8,000 characters by rewriting `AfterToolCallEvent.result` (spec non-goal mitigation).
   - Tests, in this order:
     1. Spike-confirm the uncertain behaviors: cancel during an in-flight MCP tool (AfterToolCallEvent shape); a cancelled model call producing no `llm_call` step; `cancel_tool` giving an error result.
     2. Then AC5 and AC15 through a `subprocess` call to `foundry run` (marked `network`).
     3. Then AC6, AC8 (both limits), AC9, AC9b, AC9c and AC9d with the fault server or scripted scripts.
     4. Then the CLI and config checks:
        - AC1: unknown prompt id, bad `prompt_id` line, and a missing or invalid config, each exiting 2 with no run directory and the message checked
        - AC2: exit codes 0, 1 and 2 through the subprocess
        - AC4: `run.json` is `running` with `limits` during a run (read from a hook in an in-process test), and `run.json` is single-line. The `scores.json` single-line check is in slice 4.
        - AC7: tokens and cost, and a model with no price giving null cost
        - the 8,000-character tool-result cap
        - an MCP `command` not found on PATH is handled like AC9c: the run ends `failed`, exits 1, and has a `note` step naming the server and the missing command.
     5. Test placement for AC30: only AC5 and AC15 (subprocess runs against `config/offline*.toml`) and one RODA error-detection test carry the `network` marker. AC6, AC8, AC9, AC9b, AC9c and AC9d run against `tests/mcp_fault_server.py` only, using test-local configs that name no RODA server. Slice 3 ends by running `uv run pytest -m "not network"` with network access disabled (`HTTPS_PROXY=http://127.0.0.1:9`, `UV_OFFLINE=1`) to prove the split.
   - If a confirmed behavior differs from the design, update the design before continuing.
   - Commit. Further commits within the slice are fine.
4. **Evaluation (AC10–AC14, AC10b–e, AC17–AC19, AC7b).**
   - `evaluate/view.py`, `workflow.py`, `judge.py`, `summarize.py` (which owns both the narrative and the deterministic `struggles(steps, run) -> list[str]`), `report.py`, `evaluate.py`.
   - `src/foundry/templates/judge-v1.txt` and `summarizer-v1.txt`.
   - The lock and atomic multi-file write.
   - `foundry evaluate` with `--config`, `--rubric`, `--force` and `--lock-held`.
   - Tests:
     - exact workflow scores for each fixture, recorded in the test, plus the clean > recovered > failed ordering
     - judge happy path, the malformed-twice case, and the raises case, using the scripted provider as the judge
     - the injection data-block assertion
     - hostile escaping
     - `none` output skipping the judge
     - re-evaluation leaving the immutable files unchanged
     - rubric snapshot and override
     - refusing a `running` run, and `--force`
     - replay through a `subprocess` call to `foundry evaluate`
     - AC7b: summarizer failure, and a narrative over 150 words twice
     - AC10: the report has one timeline row per trace step for each fixture. Struggles lists every errored call, retry, invalid call and limit note in `recovered` and `failed`. The narrative is 150 words or fewer.
     - the capped view: output truncated at 20,000 characters, and a 250-step synthetic trace giving the first 100 and last 100 steps plus the elision note
     - AC13 and AC10d exact strings: `Incomplete: output not scored`, `Output not scored: <reason>`, the `--force` report sentence, and `"forced": true`
     - AC2: `foundry evaluate` with an invalid `--rubric` exits 2 through a subprocess
     - AC17: replay starts no agent and no MCP server (asserted by monkeypatching the runner and the MCP builder to raise), and `run.json` is byte-identical, including `mode`
     - `scores.json` is single-line (AC4)
     - AC10d: a run directory that fails validation exits 2
     - AC10e: bad scoring weights or a missing `[judge]` exits 2 naming the key; config path, judge id and summarizer id are recorded in `scores.json`
     - AC12: judge identity, plus evaluation tokens and cost
     - AC14: every Provenance item is present
     - an invalid rubric is rejected before any model call (the scripted judge records zero calls)
     - a rubric_id mismatch warns and records both ids
     - a judge equal to the agent model prints a warning
     - two concurrent evaluations: the second exits 2 with the lock message
     - interrupted-evaluation recovery: a manufactured hash mismatch, then re-evaluating fixes it
   - `atomic_write_many` is the slice 2 `runstore` function, reused here.
   - Commit.
5. **OTel, S3, secrets (AC16, AC16b, AC20, AC21).**
   - `tracing/otel.py`: private TracerProvider, a span per step, a bounded flush, and a warn-once exporter wrapper.
   - `config/offline-otel.toml`.
   - The real `storage.mirror`: a lazy boto3 client, dotfiles excluded, warn-once on failure.
   - Tests:
     - a local recording HTTP server on an ephemeral port, with a temp config copy
     - a config with only `["jsonl"]` sends zero requests to the recording server (AC16)
     - an unreachable endpoint, timed
     - botocore Stubber for S3, plus the unset case creating no client
     - an upload failure (Stubber error) gives exactly one stderr warning naming the bucket, the exit code is unchanged, and the local files remain
     - upload timing: a recording stub sees no upload while the run is `running`, one batch at run end, and one after evaluation
     - the sentinel secrets test across the run dir, stdout/stderr, exported spans and UI pages (the UI part is added in slice 8)
   - Commit.
6. **Bedrock provider (groundwork for L1–L3).**
   - `models/bedrock.py`: `complete()` through the Converse API with a per-role botocore Config, and `strands_model()` returning a BedrockModel.
   - `config/bedrock.toml` and `bedrock-alt.toml`.
   - Tests:
     - Stubber-based tests of `complete()`: success, a throttling error after retries, a usage mapping
     - a construction test checking BedrockModel config values
   - Commit.
7. **Infra (AC22).**
   - `infra/app.py`, `infra/stack.py`, `infra/cdk.json` (`"app": "uv run python app.py"`), and `infra/package.json` pinning `aws-cdk@2.1146.1`.
   - `tests/test_infra.py`, using `Template.from_stack` assertions for:
     - region `us-west-2`
     - the bucket's BlockPublicAccess (all four true), encryption, versioning, and the SSL-only `aws:SecureTransport` deny in the bucket policy
     - IAM actions exactly `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`
     - S3 actions limited to the bucket and the `runs/*` prefix, with no other S3 resource
     - tags
     - no 12-digit account id literal anywhere in the synthesized template (the account comes from `CDK_DEFAULT_ACCOUNT`)
   - Run `cd infra && npx cdk synth` once and record the result.
   - Commit.
8. **UI (AC24–AC29).**
   - `ui/app.py`: `create_app(runs_dir, project_root)`, with these parts:
     - host, origin and CSRF middleware
     - routes `GET /`, `GET /runs/{run_id}`, `POST /runs`, `POST /runs/{run_id}/evaluate`, `GET /jobs/{job_id}/log`
     - a job table with monitor threads
     - UI-held evaluation locks
   - `ui/render.py`, using markdown-it-py with html off, images off and safe links.
   - Jinja2 templates following the approved wireframe: cards, then the report page.
   - `foundry ui`, which runs uvicorn on 127.0.0.1.
   - Tests use FastAPI TestClient against `create_app` with a temp runs dir seeded from fixtures. They cover:
     - every UI state in the design's *Experience* table, with the spec's AC24 to AC29 strings as the exact copy
     - the AC26 and AC27 denials
     - the AC28 and AC29 checks, and the 409s
     - the sentinel test extended to UI pages
   - Commit.
9. **Docs (AC31).**
   - README sections:
     - prerequisites (asdf, uv, Node)
     - Quickstart (offline)
     - running with the OTel tracer and Phoenix
     - swapping the model
     - replay
     - deploy (L4)
     - the recorded K-5 decision (Strands, CDK Python, JSONL+OTel, FastAPI)
   - Commit.
