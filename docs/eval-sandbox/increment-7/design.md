# Increment 7: multi-agent mode — Design

**Spec:** ./spec.md   **Last updated:** 2026-10-30
**Status:** Current (design gate: 3 passes; pass-3 blockers fixed and taken to the user)

## Overview

A pipeline run uses the same harness as a single run: the same model instance, the MCP servers, one shared `TraceHooks` (so limits are summed), and the same tracers. It invokes three Strands `Agent`s in sequence instead of one. Before each agent starts, the harness writes an `agent_step` start marker and points `TraceHooks.parent` at it. Every step the agent produces then carries `parent`. Step numbers keep increasing in file order, so the trace contract, the validator, the live monitor and both scorers work unchanged. The run page groups steps under their agent as bands. The dashboard keys models by "model · mode".

## Experience

**Design review: approved by the user (2026-10-30), option A, "Agent bands"**

```
Waterfall                        0s      10s      20s     30s
▾ planner        1 call   4.1s   █████
    1 model                       █████
▾ executor       11 calls 18.2s       ██████████████████
    3 model                            ███
    4 roda/search_datasets                ▌
    …
▾ checker ✗ revise  1 call 3.0s                          ███
   15 model                                                ███
▾ executor (revision 1)  1 call 2.5s                         ██
▾ checker (revision 1) ✓ approve 1 call 1.2s                    █
Feed:  [executor] 6 model  "Confirmed NAIP 2022 coverage…"
Dashboard leaderboard: "kimi-k3" and "kimi-k3 · pipeline" rows
```

- **Alternatives not chosen:**
  - B, agent filter chips over a flat waterfall
  - C, a three-bar agent Gantt above a flat waterfall
- **Run page states:**
  - **Single-agent runs** (no `agent_step`): unchanged.
  - **Running pipeline:** the current agent's band runs to "now", and the existing "working…" row follows its last child.
  - **An agent with no calls yet:** a header row with "0 calls", and a band of minimum width at its start time.
  - **Failed pipeline:** bands render whatever was recorded. The failing step is red as today, and the run status pill says failed.
  - **The 'verdict' note:** shown in the feed under the checker. The header shows ✓ approve or ✗ revise, read from the note whose `parent` is that checker step.
- **Additions to the approved wireframe** (told to the user):
  - In the feed, an `agent_step` is a divider row, `▸ planner started`, with its input preview in a collapsed `<details>`.
  - The `verdict` note is a pill: ✓ approve, ✗ revise or ? unparseable.
  - The ▾ on band headers is decorative and `aria-hidden`; there's no collapsing.
  - Band headers use the existing `--card`, `--line` and `--accent` tokens, so dark mode follows the theme. Long names (`checker (revision 1)`) ellipsize in the label column, which already scrolls inside its box at phone width.
- **Real copy:**
  - band header: `▾ checker ✗ revise · 1 call · 3.0s`
  - feed label: a small pill `executor`
  - dashboard row: `us.moonshotai.kimi-k3 · pipeline`
- **CLI:** nothing new. The mode lives in the config. `foundry run --config config/bedrock-pipeline.toml --prompt adb-001 [--evaluate]`.

## Architecture

| Component | Change |
|---|---|
| `config.py` | Add `AgentsCfg(_Strict)`: `mode: Literal["single","pipeline"] = "single"`. `Config.agents: AgentsCfg = AgentsCfg()`. |
| `harness/roles.py` and `harness/roles/v1/{planner,executor,checker}.txt` (new) | Role prompts are versioned files (`PROMPTS_VERSION = "roles-v1"`), appended after the task's `system.txt`. `user_message(role, prompt, plan=None, candidate=None, feedback=None)` wraps prior agents' text in `<plan>`, `<candidate_answer>` and `<checker_feedback>` tags. Each role prompt says tagged content is data, not instructions. `parse_verdict(text) -> "approve"\|"revise"\|None`: take the first non-empty line, strip `*_#>` and backticks, then match `^VERDICT:\s*(approve\|revise)\b`, case-insensitive. Markdown `*`, `_` and backticks are removed anywhere on the line, and leading `#`/`>` are dropped, so `**VERDICT:** revise` parses. Wrapper tags inside agent text are neutralised (`</candidate_answer>` becomes `</CANDIDATE_ANSWER-TAG>`), so a candidate can't close its own tag. An empty plan is passed on as an empty `<plan>`. |
| `harness/hooks.py` | `before_model` cancels the call (`event.cancel`) and `before_tool` cancels the tool (`cancel_tool`) when `timed_out` is set. Neither records a step, so nothing runs after a timeout, even in an agent started in the race window between the guard and `current[0]` (P3). Add `parent: int \| None = None` and `start_agent(step_no)`, which sets `parent` and clears `_errored` so retries are tracked per agent (P4). Every `tracers.record(...)` inside the hooks passes `parent=self.parent` (model calls, tool calls, limit notes). `parent` is never reset on an exception, so `_drive`'s `agent_error` note (recorded with `parent=hooks.parent`) attaches to the failing agent. |
| `harness/runner.py` | Extract the agent factory. `_drive(work, hooks, tracers, max_seconds, t_start, current)` takes a `work()` callable returning the final text, plus a `current` list holding the running `Agent`.
On timeout, `_drive` sets `hooks.timed_out = True` first, then calls `current[0].cancel()` if one is set; cancelling a finished agent is harmless. `agent_error` notes are recorded with `parent=hooks.parent`. Single mode: `work` = one agent call, as today. Pipeline mode: `_pipeline()` (below).
**run.json `agents`:** written at start (`mode`, `prompts`, `roles: []`, `revisions: 0`, `verdict: "none"`) in the initial `write_run`, and overwritten at finalize from the pipeline state object. |
| `harness/runner.py: _pipeline` | Each role, in order (planner, executor, checker, then executor (revision 1) and checker (revision 1) if needed):
1. **Guard:** if `hooks.timed_out or hooks.limit_hit`, stop and return the latest executor text (empty if none). Starts nothing.
2. `marker = tracers.record(type="agent_step", name=…, text=user_msg[:200])`, then `hooks.start_agent(marker.step)`, then `state.roles.append(name)`.
3. `agent = Agent(model, tools=… or [], system_prompt=task_system + role_prompt, hooks=[hooks], callback_handler=None, retry_strategy=None)`. Set `current[0] = agent` and call `result = agent(user_msg)`. Exceptions propagate to `_drive`, which handles them as in single mode.
4. If `result.stop_reason == "cancelled"`, or the guard condition now holds, stop.
5. **Checker:** `v = parse_verdict(text)`, then record `note` `verdict` with text `v or "unparseable"` and the checker's parent. If `v` is None, also record `note` `verdict_parse` with text `checker verdict unparseable`. Set `state.verdict`. Revise only if `v == "revise"` and no revision has run yet.

The run's final text is the latest non-empty executor text: an empty revision falls back to the first answer. Status is derived as in single mode. |
| `tracing/otel.py` | Keep `agent_spans: dict[int, Span]`. On `agent_step`, start a span `invoke_agent <name>` under the root, and store its end as its start. Children with `parent` in `agent_spans` are created in that span's context, and the agent span's end is extended to `max(end, child end)`. At `end_run`, end every agent span at its recorded end, before ending the root. |
| `ui/monitor.py` | `rows` gains band headers. **Ordering:** walk the steps in step order. In a pipeline run (`run.json.agents.mode == "pipeline"`, type-checked), every `agent_step` emits its header (a "0 calls" band at its start when it has no descendants), then its descendants in step order. In any other run, `agent_step` rows are skipped as today; imported traces render unchanged. Parentless `llm_call`/`tool_call` rows keep their position. Notes never appear in the waterfall (as today), only in the feed. For each band: its descendants (by `parent`), extent = min start to max end (or its own start if none; running, its extent reaches now), call count, and verdict from a child `verdict` note. Rows are ordered by agent, then by children in step order. Steps with no parent stay top-level in step order. Without any `agent_step`, the output is byte-identical to today. |
| `ui/templates/_monitor.html`, `_feed.html`, `base.html` | Header row markup (`wf-row agent`), an indent class, the feed agent pill, and styles. |
| `ui/dashboard.py` | The model key is `model` plus `" · pipeline"` when `run.agents.mode == "pipeline"` (type-checked). It's used for the leaderboard, the scatter and the `?model=` link (the index filter matches the same key). |
| `ui/app.py: _cards`, `_compare_side`, `compare.html` | Card `model` text and the compare header use the same `model_key(run)` helper (in `dashboard.py`), so the filter link matches. The compare page's timelines use the same band grouping (S4). |
| `config/bedrock-pipeline.toml`, `config/offline-pipeline.toml` | A copy of `bedrock.toml` or `offline.toml` with `[agents] mode = "pipeline"`. The offline one points `script` at `tasks/adb/scripts/scripted-pipeline/{prompt_id}.json`. |
| `tasks/adb/scripts/scripted-pipeline/*.json` | Six scripts. Each one: a planner text turn, executor tool turns plus text, and a checker turn `VERDICT: approve`. adb-002 adds a revise, then executor and checker turns. |
| `docs/trace-contract.md`, README | `parent`, `agent_step` semantics, `run.json.agents`, and how to run pipeline mode. |

## Data and contracts

- **trace.jsonl:**
  - `agent_step` start markers: `{"step": n, "type": "agent_step", "name": "planner", "duration_ms": 0, "status": "ok", "text": "<input preview>", "started_at": ...}`
  - Child lines carry `"parent": n`.
  - `{"type": "note", "name": "verdict", "text": "approve"|"revise"|"unparseable", "parent": <checker step>}`
  - When unparseable, also `{"type": "note", "name": "verdict_parse", "text": "checker verdict unparseable", "parent": <checker step>}`
- **run.json:** an additive optional key: `agents: {"mode": "pipeline", "prompts": "roles-v1", "roles": [<agent_step names started, in order, incl. "(revision 1)">], "revisions": 0|1, "verdict": "approve"|"revise"|"unparseable"|"none"}`. `verdict` is the last checker's, and `none` means no checker finished. It is written at start and overwritten at finalize. `agents.mode` is unrelated to the run-level `mode` (`live`/`replay`). `RunRecord` is `_Open` (extra allowed), so no model change is needed. Readers that don't know the key ignore it.
- **Dependants:**
  - The importer reads only OTel and ignores `parent` in our JSONL. Our OTel export now nests children under agent spans. The importer treats a nested `invoke_agent` as one `agent_step` without its insides (`importer.py:244`), so re-importing an exported pipeline run loses the inner calls. That's accepted and recorded (non-goal; the follow-up is to flatten nested agents in the importer).
  - The scorers read `steps` by type, so they're unaffected.

## Decisions

### ADR-1: `agent_step` as a start marker
- **Context:** Steps are numbered when they're recorded, and the validator requires numbers to increase in file order (`contract.py:236`). A child must reference its parent's number while the parent is still running, and steps are immutable once appended to JSONL.
- **Options:**
  - A) Record `agent_step` at start with `duration_ms: 0`, and derive its extent from its descendants.
  - B) Reserve a number at start and write the step at the end with its real duration. This breaks the increasing-number rule, so the validator, contract and readers would change.
  - C) Buffer each agent's steps and write them all when it ends. This breaks the live monitor.
- **Decision:** A.
- **Why:** It's the only option that keeps the contract version, the validator and live streaming unchanged. The UI and OTel derive the extent, and the contract documents it (T4).
- **Reversibility:** costly once pipeline traces exist, because old traces would keep the marker semantics. It's additive, so readers can support both.
- **Sources:** `src/foundry/contract.py:236` (the increasing-number rule); `docs/trace-contract.md` (the `parent` example).

### ADR-2: Our own sequential orchestration, not Strands multi-agent
- **Context:** Strands 1.62.0 ships `strands.multiagent` (`graph.py`, `swarm.py`) and supports agents as tools.
- **Options:**
  - A) Three sequential `Agent` calls driven by our code.
  - B) A `strands.multiagent.Graph` with three nodes and a conditional edge.
  - C) A planner agent that calls executor and checker as tools.
- **Decision:** A.
- **Why:**
  - It's deterministic: a fixed order with exactly one revision.
  - The existing hooks, limits, cancel and tracing work with no new event types.
  - The trace has one level of nesting.

  Option C makes the flow model-driven, which is harder to compare across models. Option B adds node and event types we'd have to trace and cancel separately.
- **Reversibility:** easy, because it's internal to the runner.
- **Sources:** the installed `strands/multiagent/` (1.62.0); `strands/agent/agent.py:514,585` (each Agent builds its own `HookRegistry` and calls `add_hook` for each provider, so one `TraceHooks` can serve several agents); `agent.py:647` (`cancel()` is thread-safe).

### ADR-3: Limits are shared across agents
- **Context:** Per-agent limits would let a pipeline spend three times a single run's budget.
- **Decision:** One `TraceHooks`, so `max_model_calls`, `max_input_tokens` and `max_seconds` are pipeline-wide (P3).
- **Why:** It's a fair comparison against single mode at the same budget.
- **Reversibility:** easy.
- **Sources:** `harness/hooks.py` (the counters live on the hook instance).

### ADR-5: Contract additions that are costly to change
- **What:** `run.json.agents` (its keys and values), `trace.jsonl` `parent`, the `verdict`/`verdict_parse` note names, and the OTel span name `invoke_agent <name>`.
- **Reversibility:** costly. They're additive and optional, so old readers ignore them, but once pipeline runs are mirrored to S3, renaming them needs a reader that accepts both.
- **Decision:** freeze them as written here. The trace contract documents them (T4), and the contract version is unchanged because the change is additive.

### ADR-3a: A limit hit after an answer keeps the answer (user decision, 2026-10-31)
- **Context:** the design gate's third pass asked whether a checker or revision that hits the shared limit should fail a run that already has an answer.
- **Decision:** the user chose to keep it: status `succeeded` with the last executor answer. `_drive(…, keep_answer_on_limit=True)` applies in pipeline mode only. Single mode is unchanged, and any limit there fails the run.
- **Reversibility:** easy.

### ADR-4: Retries are tracked per agent
- **Context:** `retry_of` feeds the recovery and retries scoring. A checker re-running the executor's failed call is verification, not a retry.
- **Decision:** reset the failed-call map at each agent's start (P4).
- **Reversibility:** easy.
- **Sources:** `harness/hooks.py` (`_errored`).

### ADR-6: Refusing calls after a timeout
- **Decision:** `before_model` sets `event.cancel` and `before_tool` sets `event.cancel_tool` once `timed_out` is set.
- **Sources:** the installed Strands 1.62.0 `strands/hooks/events.py:63` (`BeforeModelCallEvent.cancel: bool | str`) and `:234` (`BeforeToolCallEvent.cancel_tool`).

## Failure modes

| Failure | Detection | Behavior | Recovery |
|---|---|---|---|
| The process is killed before `end_run` | — | The OTel agent spans and the root span are never exported, as in single mode today. trace.jsonl has everything up to the kill | Accepted |
| A limit is hit mid-pipeline | `hooks.limit_hit` | Stop the pipeline; the run fails, as in single mode | Raise the limits |
| Timeout | `_drive` join timeout | Set `timed_out`, then cancel the current agent. The pipeline's guard starts no further agent, and a `cancelled` stop reason ends the loop. Status `timeout`, as today | — |
| Timeout between agents | `timed_out` set while no agent is mid-call | The guard runs before the next agent's `agent_step`, so nothing starts | — |
| Planner or checker raises | exception in `work` | An `agent_error` note with `parent`; the run fails | Inspect the trace |
| The executor returns empty text | empty final | The run fails, as today | — |
| An unparseable verdict | `parse_verdict` returns None | A `note` "checker verdict unparseable", treated as approve | — |
| The scripted script runs out | `ScriptExhausted` | The run fails, as today | Fix the script |

## Security and privacy

- **No new entry points, network endpoints or dependencies.** The role prompts are ours.
- **Agent outputs passed to the next agent are untrusted model text,** as tool results already are. The checker is read-only: it gets the same discovery tools, which take only read actions.
- **The UI renders agent names and text autoescaped,** with model text through the existing safe Markdown filter. Agent names come from a fixed set in our code, but they're still escaped.

## Performance

- A pipeline makes at least 3 model calls. With `max_model_calls` = 12, the executor's room shrinks. That's accepted as part of the fair comparison (ADR-3). `bedrock-pipeline.toml` keeps the same limits.
- Monitor grouping is O(steps).

## Migration and rollback

Everything is additive. The default mode is `single`. A rollback is a revert:
- Old pipeline runs keep rendering as flat steps with `parent`, and the scorers are unaffected.
- On the dashboard, pipeline runs merge back into their model's single row.

## Traceability

| AC | Component(s) | Test(s) |
|---|---|---|
| P1, P2 | `runner._pipeline`, `roles.py` | `test_pipeline.py::test_offline_pipeline_approve`, `::test_revision_on_revise`, `::test_unparseable_verdict_is_approve`, `::test_single_mode_unchanged` |
| P3 | shared `TraceHooks`, guards, `_drive`, `before_model`/`before_tool` refusal | `::test_limits_are_pipeline_wide`, `::test_planner_error_fails_run` (the `agent_error` note's parent is the planner), `::test_timeout_mid_pipeline_starts_no_further_agent` (a fault-server tool that sleeps past a 2 s `max_seconds` in the executor, as the existing robustness tests do: status timeout, no checker `agent_step`, the scripted model's turn index stops advancing), `test_hooks_refuse_after_timeout` (deterministic: `timed_out` set before a model and a tool event, both cancelled, no step recorded) |
| P4 | `hooks.start_agent` | `::test_retries_are_per_agent` |
| P2 parse | `roles.parse_verdict` | `::test_parse_verdict` (Markdown, case, junk) |
| T1, T2 | `runner`, `hooks.parent` | `::test_trace_nesting_and_validation` |
| T3 | `otel.py` | `::test_otel_nests_children_under_agent_spans` (in-memory exporter) |
| T4 | the contract doc | review |
| S1 | none (unchanged) | `::test_pipeline_evaluates` |
| S2 | `dashboard.py`, `_cards` | `test_dashboard.py::test_pipeline_is_its_own_row` |
| S4 | `_compare_side`, `compare.html` | `test_ui.py::test_compare_shows_pipeline_mode` |
| S3 | configs, scripts | `test_repo.py`, configs load; V1 |
| U1–U3 | `monitor.py`, templates | `test_monitor.py::test_agent_bands`, `::test_single_agent_rows_unchanged`, `::test_live_band_extends_to_now` (U3, fixed `now`); `test_ui.py::test_feed_agent_labels_and_verdict_pill`; screenshot |
| V1, V2 | — | CLI run; live Bedrock run and batch |

## Risks and spikes

- **Riskiest:** whether Kimi K3, as the checker, reliably emits `VERDICT:` on its first line. It's mitigated by P2's default and checked live in V2.
- **Second:** sharing one `BedrockModel` across sequential agents. That's low risk, because the agents run sequentially and each `Agent` keeps its own messages. V2 covers it.

**Test harness:** the pipeline tests use `harness_helpers.write_config(…, extra="[agents]\nmode = \"pipeline\"\n")` with the local fault MCP server, so CI needs no network. `config/offline-pipeline.toml` (real RODA via uvx) is exercised by V1 and is only load-checked in CI.

## Implementation plan

Each slice ends green (full suite and ruff) and is committed on `prototype`.

1. **Config and roles (P1 config, P2 parse and framing):** `AgentsCfg`; `harness/roles.py` with `roles/v1/*.txt`, `PROMPTS_VERSION`, `system_prompt`, `user_message` and `parse_verdict`. Test: `test_parse_verdict`. *Commit.*
2. **Hooks and runner (P1–P4, T1, T2):**
   - in the hooks: `parent`, `start_agent` (resets `_errored`), `_record`, and the refusal after a timeout in `before_model`/`before_tool`
   - in the runner: `_drive(work, …, current)`, `_agent`, `_PipelineState`, `_pipeline`, and run.json `agents` written at start and at finalize

   Tests in `test_pipeline.py`, via `write_config` and the fault server:
   - approve
   - revise
   - unparseable
   - single mode unchanged
   - pipeline-wide limits
   - planner error
   - timeout mid-pipeline
   - retries per agent
   - the hooks refusing after a timeout
   - trace nesting with `validate_run_dir`

   *Commit.*
3. **Configs and scripts (S3, V1):** `config/offline-pipeline.toml`, `config/bedrock-pipeline.toml`, and `tasks/adb/scripts/scripted-pipeline/adb-00{1..6}.json` (adb-002 revises). Run V1 on the CLI and evaluate (S1). *Commit.*
4. **OTel (T3):** agent spans and child nesting, with the in-memory exporter test. *Commit.*
5. **UI (U1–U4, S2, S4):**
   - `monitor.py`: ordering and bands
   - the `_monitor`/`_feed` templates: the header row, the agent pill, the divider and the verdict pill
   - `base.html`: styles
   - `dashboard.model_key`, used by the leaderboard, scatter, `_cards` and `_compare_side`

   Tests in `test_monitor.py`, `test_ui.py` and `test_dashboard.py`. Screenshots of an offline pipeline run, light and dark, desktop and 375 px. *Commit.*
6. **Docs (T4):** the trace contract, README and handoff. *Commit.*
7. **Verify (V2):** one live Bedrock pipeline run (Kimi K3, adb-001), evaluated, with a screenshot. Then a batch of `bedrock` against `bedrock-pipeline` on adb-001 and adb-003, 1 repeat, shown on the dashboard. Independent verify review. *Commit and push.*
