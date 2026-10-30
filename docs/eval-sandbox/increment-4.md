# Increment 4: sequence check, external harness, spread, compare

**Track:** Quick   **Status:** Built (verified 2026-10-30)   **Date:** 2026-10-30

## Problem

Four gaps remain in the Should and Could lists that don't need anything from Clay:

- **The workflow score ignores tool order** (E-3, and M4's "sensible order"). A run that writes a plan without confirming any dataset loses no workflow points.
- **Evaluating someone else's agent is three manual steps:** start the receiver, run the agent pointed at it, then evaluate. Clay is expected to bring "harness(es) under test".
- **Batches show only means** (D-7). Nobody can see how much a model's score varies across repeats.
- **Two runs can't be compared** without opening two reports (F-3).

## Acceptance criteria

**S: sequence check (E-3, D-1)**
- **S1:** `tasks/<task>/expected.jsonl` holds one `{"prompt_id", "stages": [[tool, …], …]}` per line. Each stage is a list of acceptable tool names, and a run satisfies the stage with any one of them. A tool may be written `server/name` to require a server, or as a bare `name` to match any server. `tasks/adb/expected.jsonl` uses bare names only, because imported runs carry no server (increment 3), so a `server/name` stage could never match one. Ids follow the prompt-id rule. A malformed line is a `RubricError`-style error that names the file and line. `tasks/adb/expected.jsonl` covers adb-001 to adb-006.
- **S2:** **Workflow scorer v2.** When the run's `(task_id, prompt_id)` has an expectation:
  - **Matching:** stages match in order against `ok` tool calls. Each stage is satisfied by the first `ok` call to one of its tools that comes after the previous stage's match.
  - **Component:** `sequence = matched / len(stages)`.
  - **Weights:** completion 0.35, reliability 0.15, recovery 0.15, validity 0.10, efficiency 0.10, sequence 0.15.

  With no expectation, the v1 weights and components apply unchanged and `sequence` is absent, so v1 scores are reproduced exactly. Either way, `scores.json.workflow_scorer` is `"v2"`, and `workflow.weights` lists the weights actually used.
- **S3:** `scores.json.workflow.sequence` is `{"stages", "matched", "missing", "expected_sha256"}`. The rationale names missing stages, e.g. `sequence (missed stage 2 of 3: get_dataset_details|preview_dataset)`. `report.md` shows the component like the other five.
- **S4:** Re-scoring a golden run with `foundry evaluate` gives a sequence of 1.0 for runs that confirmed a dataset. A trace with only discovery calls gives less than 1.0.

**X: external harness**
- **X1:** `foundry external --task T --prompt P [--config C] [--runs-dir runs] [--timeout 600] [--evaluate] -- <command…>` does four things:
  1. Starts the OTLP receiver on a free 127.0.0.1 port.
  2. Runs `<command>` as an argv list, never through a shell, inheriting the environment plus:
     - `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:<port>`
     - `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=<that>/v1/traces`
     - `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`
     - `FOUNDRY_TASK_ID`, `FOUNDRY_PROMPT_ID`, and `FOUNDRY_PROMPT` (the prompt text)
  3. Waits for the command to exit, then for pending runs to be written.
  4. Prints one run directory per line. With `--evaluate`, it also evaluates each run with `--config` (default `config/offline.toml`) and prints its score line. `--config` also supplies `[prices]`.
- **X2:** The command's stdout and stderr go to foundry's stderr, so foundry's stdout carries only results. **Timeout:** past `--timeout` seconds, the command's process group is killed, and the runs received so far are still written and printed.
- **X3:** **Exit codes:**
  - **0:** the command exited 0, and every received run `succeeded`
  - **1:** the command failed or timed out, or a received run failed
  - **2:** usage errors, an unknown prompt, a command that can't be started, **no runs received**, or (with `--evaluate`) an evaluation error. For no runs, the message names the OTEL variables that were set.
- **X4:** `examples/external_strands_agent.py` reads its endpoint from the environment and its prompt from `FOUNDRY_PROMPT` when they're set. Live: `foundry external --task adb --prompt adb-001 --config config/bedrock.toml --evaluate -- uv run python examples/external_strands_agent.py` prints a run directory and a score line.

**D: spread (D-7)**
- **D1:** The `summary.md` "Per variant" table and the UI batch page's "Per model" table gain **Min, Max and Stdev** columns, computed over the overall scores of the variant's scored runs. **Stdev** is the sample standard deviation, and shows "—" when fewer than 2 runs were scored. The "Mean overall" column becomes "Mean".

**C: compare (F-3)**
- **C1:** `GET /compare?a=<run_id>&b=<run_id>` renders the approved layout below. Invalid or unknown ids give 404 with the existing error page.
  - **Score rows:** overall, workflow plus each component, and output plus each criterion as `n/max`.
  - **Run rows:** cost, model calls, tool calls with errors, and duration.
  - **Columns:** A, B, and Δ (B − A).
  - **Timelines:** A's, then B's, each labelled with prompt and model.

  Unevaluated runs show "—" for scores.
- **C2:** Each run page gains a "Compare with" GET form: a select of the other runs, newest first, up to 200, and a Compare button. GET reads nothing beyond `runs/`, so no CSRF is needed.

## Non-goals

- No change to the output score, the judge or the rubric.
- No aligning of timelines step by step, and no JavaScript.
- `foundry external` doesn't sandbox the command. It runs what the user typed, as the user, just as the user's own shell would. It isn't exposed in the UI.
- No expectations for tasks other than `adb`. Imported runs are scored for sequence only when their task and prompt have an expectation.

## Assumptions and open questions

- **The expected stages are ours,** written from the prompts and from tools observed in real runs. Owner: the team. Default: replace them when Clay's rubric arrives, or keep them as-is.
- **OTLP exporter env vars:** `OTLPSpanExporter` in `opentelemetry-exporter-otlp-proto-http` 1.45.2 uses `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, falling back to `OTEL_EXPORTER_OTLP_ENDPOINT` + `/v1/traces` (`trace_exporter/__init__.py:139`). Strands' `setup_otlp_exporter(**kwargs)` passes its kwargs straight to that exporter (`strands/telemetry/config.py:152`).

## Plan

**Design review:**
- **Approved by the user (2026-10-30)**, two rounds:
  - **Compare page:** a delta table, then stacked timelines. Option B, refined with per-component and per-criterion rows.
  - **Batch spread:** extra columns on "Per model". Option B.
- **Alternatives not chosen:**
  - for compare, side-by-side tool columns, or criteria with judge rationales only
  - for spread, a prompt × model matrix

```
< Runs   Compare two runs
A  adb-001 · us.moonshotai.kimi-k3 · succeeded  (report)
B  adb-001 · us.anthropic.claude-sonnet-4-6 · succeeded  (report)
                    A       B       Δ (B−A)
Overall            1.00    0.96    −0.04
Workflow           1.00    1.00     0.00
  sequence         1.00    0.50    −0.50   (one row per component)
Output             1.00    0.92    −0.08
  method           4/4     3/4     −1      (one row per criterion)
Cost / Model calls / Tool calls (errors) / Duration
A timeline — <model>   | Step | Type | Name | Status | ms | Detail |
B timeline — <model>   (same)
Run page header: Compare with [ run ▾ ] [Compare]
Batch Per model: Variant | Model | Succeeded | Mean | Min | Max | Stdev | Mean cost/run
```

**Slices, riskiest first:**
1. **S1–S4:** in `tasks.py`, add `load_expected`; in `workflow.py`, v2 with the sequence component and rationale. Wire it in `evaluate.py`, add the report row, and update tests that pin v1 weights for adb prompts.
2. **X1–X4:** add `src/foundry/external.py`, which uses `Receiver` with an idle wait. Add the CLI subcommand and update the example. Tests use the real Strands agent from `tests/strands_otel_agent.py` and a non-emitting command. Then a live run.
3. **D1:** batch aggregates plus `summary.md`, and the batch template.
4. **C1–C2:** a compare route, template and run-page form, with UI tests.
5. **Docs:** README sections, then verification recorded here.

## Verification (2026-10-30)

**Suite:** 200 passed, ruff clean, and gitleaks found no leaks. There are no new dependencies.

| AC | How it was proven |
|---|---|
| S1 | `test_adb_expectations_cover_every_prompt` (every adb prompt; bare names only), and `test_expected_file_errors_name_file_and_line` (5 malformed cases, each naming `expected.jsonl:2`) |
| S2 | `test_sequence_full_match_uses_v2_weights`, `test_sequence_counts_only_ok_calls_in_order`, `test_sequence_server_qualified_names` and `test_v2_score_value` (0.925). The v1 tests still pass unchanged when there's no expectation. |
| S3 | The `sequence` block and the rationale text are asserted. The report shows the component row, the matched stages, and the expected-stages sha256 in its provenance (`test_eval_only_config_accepted_and_recorded`). |
| S4 | `test_golden_runs_confirm_datasets` (all 4 golden runs score 1.0) and `test_discovery_only_trace_loses_sequence` (0.5). A real offline batch: `offline-alt` on adb-001 only discovers, so its sequence is 0.5 and its workflow 0.925. |
| X1, X2 | `tests/test_external.py` covers 6 cases: a real Strands agent through the CLI, with prompt and endpoint from the environment and `--evaluate`; stdout carries only results; a timeout kills the command; argv only, no shell. |
| X3 | Exit 1 for a command exiting 3 after sending a run; exit 2 for no runs (the message names the variables), no command, an unknown prompt, or a missing executable |
| X4 | **Live:** `foundry external --task adb --prompt adb-006 --config config/bedrock.toml --evaluate -- uv run python examples/external_strands_agent.py` printed a run directory and `overall 0.91 \| workflow 0.82 \| output 1.00`, exit 0. The example agent has only RODA, so it never called `search_items`, and sequence is correctly 0. |
| D1 | `test_spread` (stdev needs 2 runs) and the summary-table and batch-page assertions. In a real offline batch, `summary.md` shows Mean, Min, Max and Stdev for both variants. |
| C1 | 4 UI tests: score, component and criterion rows with signed deltas; unevaluated runs show "—"; bad and traversal ids give 404; hostile `scores.json` values are escaped and type-checked. **Real server:** a fresh `foundry ui` served `/compare` for two live adb-006 batch runs (Kimi K3 vs Sonnet 4.6): overall 1.00 vs 0.96, feasibility 4/4 vs 3/4, cost $0.152 vs $0.168, and both timelines. A bad id returned 404. |
| C2 | `test_run_page_compare_form`: the select lists every other run but not the current one, and there's no form when there's nothing to compare. On the real server the select listed the other runs, newest first. |

**Design review compliance:** the built pages match the approved wireframes. Two small differences: the compare page adds a "not evaluated yet" note when a run has no scores, and the batch page adds a one-line caption above "Per model".

**Found and fixed during build:**
- A `server/name` stage can never match an imported run, because imported tool calls carry no server. `tasks/adb/expected.jsonl` uses bare names, and a test enforces it. The live X4 run exposed this.
- Re-evaluating changes workflow scores for adb runs, since v2 adds the sequence component. Two pinned test values moved: the `recovered` fixture went from 0.90 to 0.925, and its overall from 0.78 to 0.80. Runs scored earlier keep their v1 `scores.json` until re-evaluated, and the compare page shows them without a sequence row.
