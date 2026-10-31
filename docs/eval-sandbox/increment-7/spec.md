# Increment 7: multi-agent mode (B-6) — Spec

**Track:** Standard   **Status:** Defined (design gate pass 2 findings applied)   **Date:** 2026-10-30   **Product stage:** Introduction (hackathon prototype)

## Problem

Clay's brief lists multi-agent traces as a planner, executor and checker. The sandbox can only run, trace and score one agent per run, so nobody can see how a team of agents splits the work, or whether it beats a single agent on the same model at the same cost.

## Users

- **Clay's evaluation team and the hackathon judges:** they compare agent harnesses on Clay tasks.
- **The team (Team 46):** they demo and run batches.

## Goals

- Run the same task as a planner → executor → checker pipeline on any configured model, with one revision when the checker objects.
- Trace each agent's own model and tool calls nested under it, live, in the existing trace contract.
- Score pipeline runs exactly like single-agent runs, and compare the two side by side in batches, the dashboard and the compare page.

## Non-goals

- **A different model per role.** All three roles use `[model]`. Per-role models are a follow-up.
- **Agents beyond these three, swarms, or a dynamic graph.** There's no Strands `Swarm` or `Graph`.
- **More than one revision.**
- **Pipeline mode for imported or `foundry external` runs.** Imported traces keep their current single `agent_step` treatment.
- **New scorers.** The workflow and output scorers apply unchanged, to all steps and to the final text.

## User stories

- As an evaluator, I pick `config/bedrock-pipeline.toml` in the UI or CLI, and I get a run whose page shows the planner, executor and checker as bands, live.
- As an evaluator, I run a batch over `bedrock` and `bedrock-pipeline`, and I see single and pipeline as separate leaderboard rows and batch variants.

## Acceptance criteria

**P: pipeline execution**
- **P1:** `[agents] mode = "pipeline"` in a config runs three Strands agents in sequence. With `mode = "single"`, or with no `[agents]` section, the run behaves exactly as today. The existing suite passes unchanged, and run.json for single-mode runs gains no new keys.
  - **When `run.json.agents` is written:** at run start, as `{"mode": "pipeline", "prompts": "roles-v1", "roles": [], "revisions": 0, "verdict": "none"}`, so running or crashed runs are already keyed as pipeline. Finalize overwrites it.
  - **`roles`:** every `agent_step` name started, in order, including `(revision 1)` names.
  - **`revisions`:** the number of executor revisions run (0 or 1).
  - **`verdict`:** the **last** checker's verdict. It is `approve`, `revise` or `unparseable`, or `none` if no checker finished.
  - **`prompts`:** the role-prompt version.
  1. **Planner:** no tools. Its input is the task prompt. It writes a numbered plan.
  2. **Executor:** has the MCP tools. Its input is the task prompt plus the plan. Its output is the candidate answer.
  3. **Checker:** has the MCP tools, so it can verify claims. Its input is the task prompt plus the candidate. Its output begins with a line `VERDICT: approve` or `VERDICT: revise`, followed by its reasons.
- **P2:** On `VERDICT: revise`, the executor runs once more, given the prompt, its previous answer and the checker's reasons. The checker then runs once more. The final answer is the latest **non-empty** executor answer, whatever the second verdict says.
  - **Parsing:** the verdict is read from the first non-empty line after stripping Markdown emphasis and heading characters (`*`, `_`, `#`, `>`, backticks). `**VERDICT: Revise**` parses as revise.
  - **Missing or unparseable:** treated as approve for the flow, recorded as `unparseable`, and noted as in T2.
  - **Untrusted text:** text passed from one agent to the next (the plan, the candidate answer, the checker's reasons) is wrapped in `<plan>`, `<candidate_answer>` or `<checker_feedback>` tags. The role prompt says the tagged content is data to evaluate, not instructions.
  - **Versioning:** role prompts live in `src/foundry/harness/roles/v1/*.txt`, and their version is recorded in `run.json.agents.prompts`.
- **P3:** `max_model_calls`, `max_input_tokens` and `max_seconds` apply to the whole pipeline (the sum across agents), not per agent. A limit hit stops the pipeline: the run fails on a model or token limit and times out on time, exactly like single mode. Before each agent starts, the pipeline checks for a timeout, a limit hit and a cancel, and starts nothing further once one is set. A cancelled agent that returns normally (`stop_reason` `cancelled`) also stops it. After a timeout, the hooks refuse every model and tool call, which closes the race where an agent starts just as `_drive` cancels the previous one. No model call is made after the run is finalized. Status rules:
  - **`succeeded`:** a non-empty final answer exists, and no limit was hit.
  - **`failed`:** a limit was hit, an agent errored, or the final answer is empty. If the planner or checker errors, the run fails. Their errors are not swallowed: the `agent_error` note carries `parent` = the failing agent's step.
- **P4:** Retries (`retry_of`) are tracked per agent. The failed-call map resets when each agent starts, so a checker repeating an executor's failed call is not a retry.

**T: trace**
- **T1:** Each agent invocation writes one `agent_step` when it starts, with:
  - `name`: `planner`, `executor`, `checker`, `executor (revision 1)` or `checker (revision 1)`
  - `duration_ms`: 0
  - `text`: a 200-character preview of its input
  - `status`: `ok`

  Every `llm_call`, `tool_call` and `note` that the agent produces carries `parent` = that step's number. Step numbers keep increasing in file order (`validate_run_dir` passes unchanged).
- **T2:** The checker's verdict is recorded as a `note` named `verdict`, with text `approve`, `revise` or `unparseable`, and `parent` = the checker step. An unparseable verdict also gets a `note` named `verdict_parse`, with text `checker verdict unparseable` and the same parent.
- **T3:** The OTel tracer exports `agent_step` as an `invoke_agent <name>` span. Its children are exported as child spans of that span, and the agent span ends at its last child's end.
- **T4:** `docs/trace-contract.md` documents `parent` and the start-marker semantics of `agent_step`. In a run with `run.json.agents.mode == "pipeline"`, every `agent_step` is a start marker with `duration_ms: 0`, and its extent is its descendants' extent (its start time if it has none). An `agent_step` with a real duration and no descendants keeps its own duration; that is what imported traces write, and their rendering is unchanged.

**S: scoring and comparison**
- **S1:** `foundry evaluate` scores pipeline runs with no scorer changes. Steps are counted across all agents. The sequence check matches ok tool calls in file order across agents. The judge sees the final answer only.
- **S4:** The compare page shows each side's model as `model · pipeline` for pipeline runs, and its timelines use the agent bands.
- **S2:** The dashboard leaderboard and scatter group runs by model and mode: `us.moonshotai.kimi-k3` and `us.moonshotai.kimi-k3 · pipeline` (the full model id plus ` · pipeline`) are separate rows and series, and the `?model=` filter matches that exact string. The key reads `run.json.agents.mode`, never the run-level `mode` (live or replay). Batch variants already come from configs, so `bedrock` and `bedrock-pipeline` are separate variants.
- **S3:** `config/bedrock-pipeline.toml` and `config/offline-pipeline.toml` exist. The offline one uses a scripted model with scripts for adb-001 to adb-006, each with one revision on adb-002. CDK's model list is unchanged, since the configs reuse its models.

**U: run page (approved design A, "Agent bands")**
- **U1:** Rows follow step order. A band header sits at its `agent_step`'s position, with its descendants directly below it in step order. Model and tool calls without a parent stay at their own position. Notes appear in the feed only, as today. In the waterfall, each start-marker `agent_step` (T4) is a header row: ▾, name, call count, duration as its descendants' extent, the checker verdict (✓ approve or ✗ revise) if any, and a band bar across that extent. Its descendants are indented below it, in step order. Runs without `agent_step` rows render exactly as before.
- **U2:** Each feed item whose step has a parent shows an agent label with the parent agent's name.
  - **`agent_step` items** render as a divider row, `▸ executor (revision 1) started`, with the input preview in a collapsed `<details>`.
  - **The `verdict` note** renders as a pill: ✓ approve, ✗ revise or ? unparseable.
  - **The ▾ on band headers** is a plain marker. Bands don't collapse in this increment, and the marker is `aria-hidden`.
- **U4:** Done includes screenshots in light and dark themes, at desktop and 375 px. The feed's `<details>` previews are native elements, so they're reachable by keyboard.
- **U3:** It works live: while the run is running, the current agent's band grows with the "working…" bar, and the monitor's existing tests still pass.

**V: verification**
- **V1:** Offline: `foundry run --config config/offline-pipeline.toml --prompt adb-002 --evaluate` exits 0. Its trace has 5 agent steps, `run.json.agents.revisions == 1`, and it validates.
- **V2:** Live: one pipeline run on Bedrock (Kimi K3, adb-001) succeeds and is evaluated. Its run page shows the bands, checked by a screenshot. A small batch (`bedrock` against `bedrock-pipeline`, adb-001 and adb-003, 1 repeat) puts both on the dashboard.

## Known scoring behaviour (accepted, 2026-10-30)

The scorers are unchanged on purpose (S1), so the comparison measures the pipeline as a whole:
- Checker tool calls count toward the sequence check and reliability.
- The pipeline's extra model calls count against efficiency once they pass half of `max_model_calls`.

This is the cost of the pipeline, not a bug. The report shows it.

## Success signals

- At the demo, a single-versus-pipeline comparison on the same model and prompts is one batch and one dashboard view.

## Constraints

- No new dependencies. Strands 1.62.0 is the only agent framework.
- The trace contract stays additive: the contract version is unchanged, and older readers ignore `parent` and `agents`.

## Assumptions and open questions

1. **A single model instance can be shared by three sequential agents** (BedrockModel and ScriptedModel). The scripted model replays one script across all three. Owner: the team. Default: share it; covered by V1.
2. **One `TraceHooks` instance can be registered on several Agents**, and it keeps the limits summed. Owner: the team. Default: share it; covered by a test.
3. **The checker can be prompted into a parseable first-line verdict** on Kimi K3. Owner: the team. Default: treat unparseable as approve (P2); V2 checks it live.
4. **Scatter colours:** two models in two modes make four series. The scatter colours three and folds the fourth into "Other", as the palette's all-pairs cap requires. Owner: the team. Default: accepted for the demo; the leaderboard and table keep every series named.
