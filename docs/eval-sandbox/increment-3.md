# Increment 3: evaluate agents we didn't build (OpenTelemetry import)

**Track:** Quick   **Status:** Built (verified 2026-10-30)   **Date:** 2026-10-30

## Problem

The organizers expect Clay to bring "harness(es) under test". `foundry evaluate` reads only our trace format, so another team's agent would have to be rewritten to emit it. Most agent frameworks already emit **OpenTelemetry GenAI spans**: Strands, which we verified, and others that follow the GenAI semantic conventions.

## Ground truth (2026-10-30)

A real Strands 1.62.0 agent (scripted model plus fault MCP server, `StrandsTelemetry` exporting over OTLP/HTTP) produced these spans, in both attribute styles. That's the default style and `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`:

- **`invoke_agent <agent name>`** is the root. It carries the model (`gen_ai.request.model`) and run totals. The final answer is in a `gen_ai.choice` event's `message` (default style), and the user prompt is in the `gen_ai.user.message` event or `gen_ai.input.messages`.
- **`chat`** is one model call:
  - Tokens: `gen_ai.usage.input_tokens` and `output_tokens`, plus the legacy `prompt_tokens` and `completion_tokens`, plus `gen_ai.usage.cache_read.input_tokens` or `cache_read_input_tokens`.
  - Output: a `gen_ai.choice` event's `message` (default style), or `gen_ai.output.messages` with typed `parts` (latest style).
- **`execute_tool <tool>`** is one tool call:
  - `gen_ai.tool.name`, `gen_ai.tool.call.id` and `gen_ai.tool.status` (`success` or `error`).
  - Arguments: the `gen_ai.tool.message` event's `content` (default), or `gen_ai.tool.call.arguments` (latest).
  - Result: the `gen_ai.choice` event's `message` (default), or `gen_ai.tool.call.result` (latest).
  - A failed tool sets span status `ERROR` with the message.
- **Noise:** `execute_event_loop_cycle`, plus `MCP send …` spans from the MCP SDK.

## Acceptance criteria

- [x] **I1:** `foundry import <file> --task <id> [--prompt-id <id>] [--runs-dir runs] [--config <file>]` reads OTLP/JSON. That's either one `ExportTraceServiceRequest` object or JSON Lines of them, which is the OpenTelemetry Collector file exporter's format. It writes **one run directory per agent invocation** (each `invoke_agent` span) and prints the paths.
  - Exit 0 on success.
  - Exit 2 with a message on unreadable input, a file over 50 MB, or no `invoke_agent` span.
  - `--config` is used only for `[prices]`, to compute `cost_usd`.
- [x] **I2:** **Mapping.** All descendants of the root become steps, ordered by start time:
  - `chat`, `text_completion` and `generate_content` become `llm_call` steps: tokens including cache, model, output text or `(calls <tools>)`.
  - `execute_tool` becomes `tool_call`: name, arguments, a ≤ 1 KB result preview, and status and error from span status or `gen_ai.tool.status`.
  - Nested `invoke_agent` spans become `agent_step`.
  - Everything else is ignored.
  - `retry_of` follows the trace contract's rule.
  - Both attribute styles above are supported.
- [x] **I3:** **`run.json`.**
  - `harness.name` comes from `gen_ai.provider.name` or `gen_ai.system`.
  - `model.id` comes from `gen_ai.response.model` or `gen_ai.request.model`.
  - The prompt is the first user message.
  - `started_at` and `ended_at` are the root span's times.
  - `output.json` holds the final answer.
  - **Status:** `failed` if the root span is `ERROR`; `succeeded` if there's a final answer; `failed` otherwise.
  - `run.json.source` is an additive field: `{"format": "otlp", "file": <path or "receiver">, "trace_id": …}`.
- [x] **I4:** Imported directories pass `foundry validate`, and `foundry evaluate` scores them unchanged, including the workflow score.
- [x] **I5:** **Round trip.** A run exported by our own OTel tracer, then imported, gives the same step sequence (type, name, status, token counts) as its `trace.jsonl`.
- [x] **I6:** **Real Strands agent.** The spike agent's spans, in both attribute styles, import to:
  - 3 `llm_call` steps and 2 `tool_call` steps (`echo` ok, `fail` error with its message)
  - tokens 500/20, 700/20 and 900/30
  - output "Final plan: use NAIP."
  - status `succeeded`
- [x] **I7:** **Untrusted input.**
  - Bad JSON or a wrong shape exits 2 and names the problem.
  - Long attribute values are truncated, and run ids are sanitized to `^[A-Za-z0-9._-]+$`.
  - Nothing in the input is executed or followed: no URLs, no file references.
- [x] **I8:** **`foundry receive [--port 4318] [--runs-dir runs] --task <id>`** is an OTLP/HTTP receiver on **127.0.0.1 only**.
  - It accepts protobuf and JSON at `POST /v1/traces`, with a 10 MB body limit.
  - It writes a run directory when an `invoke_agent` root span arrives, after a 2 s grace period for late spans.
  - Any agent can point `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318` at it.
  - Tested with the real Strands agent exporting to it.

## Non-goals

- Frameworks we can't run here. Third-party packages weren't installed (the session's safety rules). The mapping follows the GenAI semantic conventions, so it should work for other conformant emitters, but it is only *verified* for Strands and our own tracer.
- MCP server attribution for imported tool calls. Strands spans don't carry which server served a tool, so `server` stays empty.
- Scoring changes.

## Plan

1. `src/foundry/importer.py`: OTLP/JSON and protobuf parsing, span-to-step mapping, writing run dirs.
2. `foundry import` and `foundry receive` in the CLI.
3. Tests: Strands ground truth in both styles (generated in-test with the real Strands agent), round trip with our tracer, malformed input, then evaluate on an imported run, and the receiver against a real Strands agent.
4. Verify: import a live Bedrock run's Strands telemetry. Use the `bedrock.toml` agent with Strands' own telemetry enabled, then evaluate it.

## Verification (2026-10-30)

**Suite:** 160 tests pass, and `ruff` is clean. `tests/test_importer.py` has 10 tests:

- **I6:** a real Strands agent with Strands' own telemetry, in both attribute styles, run in a subprocess per style because `StrandsTelemetry` installs a global tracer provider.
- **I4:** evaluation of an imported run.
- **I5:** a round trip through our OTel tracer.
- **I2:** hand-written OTLP covering hex ids, a sub-agent as `agent_step`, two runs in one file, a failed root, legacy token names and `retry_of`.
- **I7:** bad input, the size limit, and a hostile trace id and huge values.
- **I8:** the receiver end to end with the real Strands agent, plus a 400 for bad JSON and a 404 for other paths.

**Live, an agent we didn't build:** `examples/external_strands_agent.py` is a plain Strands agent on Bedrock (Kimi K3) with the real RODA MCP server and Strands' built-in telemetry. It doesn't import foundry. It exported to `foundry receive`, which wrote a valid run directory:

- 5 model calls and 12 RODA tool calls
- `harness: strands-agents`, `agent: external-planner`
- recorded cost **$0.32**, from `[prices]`

`foundry evaluate --config config/bedrock.toml` (Opus judge) scored it **1.00**.

**Fixed during build:**
- `MessageToDict` writes trace and span ids as base64, while OTLP/JSON uses hex. Both are now accepted and normalized to hex, and unrecognized ids are reduced to letters and digits.
- Coarse timestamps could tie, so a tool call sorted before the model call that requested it. Ties now break on the recorded step number, then on end time.
- `foundry receive` now flushes its "run written" lines.

**Known limitations:**
- **Server-specific error conventions aren't visible in OTel.** RODA reports some errors *inside* a successful result (`{"status": "error"}`). Our harness detects that through `error_detection = "json_status"`, but an imported trace marks such calls `ok` unless the emitter set an error status. The workflow score is then generous for those calls.
- Imported tool calls have no MCP server name (`server` is empty); Strands spans don't carry it.
- Verified only against Strands and our own tracer. Other frameworks should work if they follow the GenAI semantic conventions, but they weren't run here.
