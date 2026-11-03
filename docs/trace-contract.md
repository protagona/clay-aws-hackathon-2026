# Trace contract (v0.2)

This is the one agreement that lets everyone work in parallel. The harness **writes** these files; the summarizer, scorer, report, and UI **read** them. Once agreed, anyone can hand-write a sample trace and build against it without waiting for the live agent.

Most fields map onto OpenTelemetry GenAI span attributes (for example, `tokens_in` becomes `gen_ai.usage.input_tokens`). `cost_usd`, `retry_of` and `result_preview` have no standard attribute and would go in as custom attributes. An OTel-based tracer can therefore emit the same data, which is the docs' suggested default.

## Files per run

```
runs/<run_id>/
  run.json        # what was asked, with what, and how it ended
  trace.jsonl     # one step per line, in order
  output.json     # what the agent produced
  rubric.json     # snapshot of the rubric/reference used (copied in at scoring time)
  scores.json     # written by the scorer
  report.md       # written by the reporter (report.html optional)
```

`runs/` lives locally and mirrors to S3 (`s3://<bucket>/runs/<run_id>/`).

## run.json

```json
{
  "run_id": "2026-11-03T14-02-11Z_adb-001_a1b2",
  "task_id": "adb-eval",
  "task_version": "0.1",
  "prompt_id": "adb-001",
  "prompt": "Build a dataset of ... using open imagery for ...",
  "model": { "provider": "bedrock", "id": "<model-id>", "params": { "temperature": 0 } },
  "harness": { "name": "<framework>", "version": "<x.y>" },
  "tracer": "jsonl",
  "mcp_servers": [{ "name": "roda", "package": "awslabs.roda-mcp-server", "version": "<pinned>" }],
  "inputs": [{ "kind": "s3", "uri": "s3://.../naip-sample.tif" }],
  "rubric_id": "adb-rubric-v0",
  "reference_ids": [],
  "mode": "live",
  "status": "succeeded",
  "started_at": "2026-11-03T14:02:11Z",
  "ended_at": "2026-11-03T14:04:52Z"
}
```

- `mode`: `live` or `replay`.
- `rubric_id` and `reference_ids` record what the run is judged against: a rubric for ADB, or example captions and retrieval sets for captions. Together with `inputs`, `model` and the MCP server versions, they are the run's provenance.
- `status`: `succeeded`, `failed`, or `timeout`.
- `agents` (optional, pipeline runs only): `{"mode": "pipeline", "prompts": "roles-v1", "roles": [agent names started, in order], "revisions": 0|1, "verdict": "approve"|"revise"|"unparseable"|"none"}`. `verdict` is the last checker's, and `none` means no checker finished. It is written at start and final at the end. It is unrelated to the run-level `mode` (`live`/`replay`).

## trace.jsonl (one object per line)

```json
{"step": 1, "parent": null, "type": "llm_call", "name": "plan", "started_at": "...", "duration_ms": 2100, "tokens_in": 1800, "tokens_out": 240, "cost_usd": 0.004, "status": "ok", "text": "I'll search RODA for NAIP..."}
{"step": 2, "parent": 1, "type": "tool_call", "server": "roda", "name": "search_datasets", "args": {"query": "NAIP"}, "result_preview": "3 datasets ...", "started_at": "...", "duration_ms": 640, "status": "ok"}
{"step": 3, "parent": 1, "type": "tool_call", "server": "roda", "name": "sample_dataset", "args": {"id": "naip"}, "started_at": "...", "duration_ms": 1200, "status": "error", "error": "AccessDenied: requester pays"}
```

- The RODA tool names above are **illustrative**. Use the real ones once B-2 lists them.
- `type`: `llm_call`, `tool_call`, `agent_step` (for multi-agent: planner, executor, checker), or `note`.
- `status`: `ok` or `error`.
- `parent`: optional, the number of an earlier step that this step belongs to. In multi-agent (pipeline) runs, every model call, tool call and note an agent produces carries `parent` = that agent's `agent_step`.
- **`agent_step` in pipeline runs** (`run.json.agents.mode == "pipeline"`) is a **start marker**: it is written when the agent starts, with `duration_ms: 0` and `text` = a 200-character preview of what it was handed: the tagged plan, candidate or feedback, or the task for the planner. Its extent is its descendants' extent (its own start time if it has none). Step numbers still increase in file order. Imported traces write `agent_step` with a real `duration_ms` and no children; that use is unchanged.
- **Pipeline notes:** `note` `verdict` (`text`: `approve`, `revise` or `unparseable`, `parent` = the checker), and `note` `verdict_parse` (`checker verdict unparseable`) when the checker's last line that starts with `VERDICT:` is missing or says something else.
- Optional `retry_of: <step>` marks a **retry** explicitly. Don't infer retries from repeated tool names, because agents legitimately call the same tool many times.
- `result_preview` is capped at about 1 KB. Large results go to a file referenced as `result_uri`.

## output.json

```json
{ "kind": "dataset_plan", "text": "...", "artifacts": [{ "uri": "s3://...", "description": "..." }] }
```

## scores.json

```json
{
  "rubric_id": "adb-rubric-v0",
  "workflow": {
    "steps": 14, "tool_calls": 9, "tool_errors": 2, "retries": 1, "recovered": true,
    "invalid_tool_calls": 0, "duration_s": 161, "cost_usd": 0.07,
    "sequence_ok": true, "score": 0.78, "rationale": "..."
  },
  "output": {
    "criteria": [{ "id": "data-fit", "score": 3, "max": 4, "rationale": "..." }],
    "score": 0.70, "judge": { "provider": "bedrock", "id": "<model-id>" }
  },
  "overall": 0.74
}
```

## Required fields

Evaluation relies only on these fields, so any harness that writes them can be evaluated with `foundry evaluate`. The validator (`foundry validate <run_dir>`) checks them.

- **`run.json`:** `run_id`, `task_id`, `prompt_id`, `model.provider`, `model.id`, `status`, `started_at`. `mode` defaults to `live`.
- **`trace.jsonl`, each line:** `step`, `type`, `name`, `started_at`, `duration_ms`, `status`. A step with `status: "error"` must also have a non-empty `error`. `step` numbers increase.
- **`output.json`:** `kind`. `text` defaults to `""`.

Every other field is optional. Readers must accept fields they don't recognize.

## v0.2 additions (additive)

These were added by the eval sandbox build ([spec](eval-sandbox/spec.md), [design](eval-sandbox/design.md)). Each is a one-way door once Clay depends on it.

### run.json

- `contract_version`: `"0.2"`. A file without it is v0.1.
- `status` can also be `running`. It is written at run start and rewritten with the final status at the end.
- `limits`: `{"max_model_calls", "max_input_tokens", "max_seconds"}`.
- `config`: the path of the config file used.
- `mcp_servers[].error_detection`: `mcp`, or `json_status` for servers such as RODA that report errors inside a successful MCP result.

- `source` (imported runs only): `{"format": "otlp", "file": <path or "receiver">, "trace_id", "span_id", "agent"}`. These are runs from another agent's OpenTelemetry spans (`foundry import` / `foundry receive`); `tracer` is `otel-import`, and `mcp_servers` is empty.

### trace.jsonl

- `tokens_in_cached` on an `llm_call`: the part of `tokens_in` served from the model's prompt cache. `tokens_in` always includes cached tokens.
- `invalid: true` on a `tool_call`: the tool didn't exist, or its arguments failed the tool's input schema. On an unknown tool, `server` is `null`.
- `retry_of` points to the **most recent** earlier errored call with the same server, name and arguments.

### output.json

- `kind: "none"` with `text: ""` means the agent produced no final answer.

### scores.json

Written by `foundry evaluate`, always together with `rubric.json` and `report.md`. The fields below extend the original draft.

**Identity and integrity**
- `contract_version`
- `evaluation_id`
- `evaluated_at`
- `rubric_sha256`, `report_sha256`: a mismatch means the evaluation was interrupted, so re-run it

**Inputs**
- `config`
- `rubric_id` (from `run.json`), `rubric_id_loaded`, `rubric_version`, `rubric_id_mismatch`
- `templates`: `{"judge", "summarizer"}`
- `workflow_scorer`: `"v1"`
- `weights`: `{"workflow", "output"}`

**Workflow**
- `steps`, `tool_calls`, `tool_errors`, `retries`
- `recovered`: `true`, `false`, or `null` when there were no errors
- `invalid_tool_calls`, `model_calls`, `max_model_calls`
- `duration_s`, `cost_usd`
- `components`, `weights`, `score`, `rationale`

**Output**
- `criteria[]`: `{id, score, max, rationale}`
- `score`: `null` if not scored
- `error`, `judge`, `rubric_version`

**Overall and summary**
- `overall`: `null` when the output wasn't scored
- `narrative`, `narrative_error`
- `struggles[]`

**Evaluation cost and crash handling**
- `evaluation`: `{tokens_in, tokens_out, cost_usd, judge, summarizer}`
- `forced`, `truncated_trace_lines`

**Path check** (clay-embeddings, 2026-11-02)
- `paths`: `null` when the check was off; otherwise `{version, checked, found, not_found, unverified, items[]}`, each item `{location, status, reason}`. `location` is `s3://bucket/key`; `status` is `found`, `not_found` or `unverified`.
- `path_checker`: `"v1"`, or `null` when off. It also marks runs whose judge input carried the `<evaluator_checks>` block.
- The key names, the item shape and the three status values are permanent under the additive-only rule. `reason` is display text: don't match on it.

## Rules

1. **Additive only.** New optional fields are fine; renaming or removing a field needs the whole team.
2. Every run directory is **self-contained**, so it can be zipped, replayed, and re-scored later (provenance). The scorer copies the exact rubric it used into `rubric.json`, so swapping in Clay's rubric later doesn't rewrite history.
3. **Migration-aware:** No database is needed for the hackathon.
   - `trace.jsonl` loads directly into Athena or Snowflake later.
   - Write `run.json` and `scores.json` as single-line JSON so they load the same way. The examples here are pretty-printed for reading only.
4. Keep 2–3 **hand-made fixture runs** in `fixtures/runs/`: one clean success, one with errors and recovery, one failure.
