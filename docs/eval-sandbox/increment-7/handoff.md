# Increment 7: multi-agent mode — Handoff

**Spec:** ./spec.md   **Design:** ./design.md   **Date:** 2026-10-31   **Track:** Standard

## What shipped

- **Pipeline mode:** `[agents] mode = "pipeline"` runs a planner → executor → checker pipeline on the configured model, with one revision when the checker says `VERDICT: revise`. The budget is pipeline-wide. If a limit is hit after the executor has answered, the run keeps that answer (user decision, 2026-10-31).
- **Trace:** each agent writes an `agent_step` start marker, and its calls carry `parent`. OTel exports each agent as an `invoke_agent <name>` span, with its calls nested under it. run.json gains `agents`.
- **UI:**
  - agent bands in the waterfall
  - agent labels, start dividers and verdict pills in the feed
  - `<model> · pipeline` as its own row on the dashboard, run cards, `?model=` filter and compare page
  - an Agent column in the compare timelines
- **Configs:** `config/offline-pipeline.toml` (scripted, with offline scripts for adb-001 to adb-006; adb-002 revises) and `config/bedrock-pipeline.toml`.
- **Commits:** `2362521` (slices 1–3), `a5ebdac` (slices 4–5), plus this verification commit.

## Decisions made during the build

- **Gate escalation:** the design gate ran 3 subagent passes. Pass 3 found 2 blockers, both fixed (verdict parsing anywhere on the line, and bands keyed on `agents.mode`). Its one open question went to the user: a limit hit after an answer keeps the answer.
- **Verdict parsing changed after the first live run:** Kimi K3 wrote its reasoning before the verdict despite the prompt. `parse_verdict` now takes the **last** line that starts with `VERDICT:`. The live batch then parsed both verdicts (approve; revise, then revise).
- **Compare page:** its timelines are tables, so S4's "agent bands" became an Agent column, and the spec was updated.

## Verification

**Suite:** 242 passed (final, after the parser change). Ruff is clean, gitleaks found no leaks, and there are no new dependencies.

| AC | How it was proven |
|---|---|
| P1 | `test_offline_pipeline_approve`: three agents, and run.json `agents` exactly as specified, including `prompts: roles-v1`. `test_single_mode_unchanged`: no `agents`, no `parent`. |
| P2 | `test_revision_on_revise`, `test_second_revise_still_ends_and_empty_revision_keeps_answer`, `test_unparseable_verdict_is_approve`, `test_parse_verdict` (Markdown anywhere, reasoning first, last line wins, junk), and `test_user_message_frames_other_agents_text_as_data` (no early tag close). **Live:** the batch run adb-001 got `revise`, then `executor (revision 1)` and `checker (revision 1)`. |
| P3 | `test_limits_are_pipeline_wide` (limit hit inside the executor: failed, no checker), `test_limit_after_answer_keeps_the_answer`, `test_planner_error_fails_run`, `test_timeout_mid_pipeline_starts_no_further_agent` (2 s, as in the existing timeout tests), and `test_hooks_refuse_after_timeout` (deterministic). **Live:** the batch adb-001 pipeline hit `max_model_calls (12)` inside `checker (revision 1)` (note 47, parent 37) and succeeded with the revised answer. |
| P4 | `test_retries_are_per_agent` |
| T1, T2 | Parent assertions in `test_offline_pipeline_approve`, and `validate_run_dir` returns `[]`. **V1:** the offline adb-002 run, checked with `foundry validate` (exit 0). |
| T3 | `test_pipeline_children_nest_under_agent_spans`: the real CLI exports to a local OTLP recorder. The three agent spans sit under the root, the executor's children sit under the executor, and each agent span ends at its last child's end. |
| T4 | `docs/trace-contract.md`: `parent`, start-marker semantics, the pipeline notes, and run.json `agents` |
| S1 | V1 offline (overall 0.81) and the live runs below were evaluated with unchanged scorers. |
| S2 | `test_pipeline_is_its_own_row`. **Live:** the dashboard shows `us.moonshotai.kimi-k3 · pipeline` (0.92, $0.296 a run, 3 runs) next to `us.moonshotai.kimi-k3` (0.97, $0.146). |
| S3 | Both configs load (`test_repo` and V1 run them). The offline scripts exist for adb-001 to adb-006. |
| S4 | `test_pipeline_run_page_compare_and_cards`: the compare header reads `· pipeline`, and the timelines have an Agent column. |
| U1, U2 | `test_agent_bands`, `test_single_agent_rows_unchanged` (an imported or flat `agent_step` is skipped, as before), and `test_pipeline_run_page_compare_and_cards` through the real runner and app |
| U3 | `test_live_band_extends_to_now_and_empty_agent_has_a_band` (fixed `now`) |
| U4 | Screenshots: offline adb-002 light at desktop, and dark at 375 px; the live batch adb-001 light at desktop. Labels ellipsize at 375 px and the waterfall scrolls inside its box. The `<details>` previews are native, so they're keyboard-reachable. |
| V1 | `foundry run --config config/offline-pipeline.toml --prompt adb-002 --evaluate`: exit 0, 5 agent steps, `revisions: 1`, valid, overall 0.81. |
| V2 | **Live Bedrock (Kimi K3):** the single run adb-001 succeeded with 0.92. It showed the checker's reasoning-first verdict, which prompted the parser change. **Batch `b-20261031-070518-a7e1`** (bedrock against bedrock-pipeline, adb-001 and adb-003, evaluated, $0.99 total): single scored 0.96 and 0.96; pipeline scored 0.93 (approve) and 0.91 (revise, then a limit). Every run mirrored to S3 by default (increment 6, S1, now proven live). |

**Exercised live:** yes. The installed CLI ran against Bedrock with the deployed bucket mirror, and the UI preview server was screenshotted with headless Chromium.

## Security scans

- **Secrets:** gitleaks at each commit, no leaks.
- **Dependencies:** none added.
- **Untrusted text between agents:** wrapped in tags declared as data, with wrapper tags inside the text neutralised (test above). The checker's tools are the existing read-only discovery tools.
- **UI:** all new fields render autoescaped. Agent names come from a fixed set, and model text goes through the existing safe Markdown filter.

## Deferred and follow-ups

- **Per-role models,** for example a cheap planner. This was a non-goal.
- **The importer loses inner calls** when re-importing an exported pipeline run: it flattens nested `invoke_agent` spans into one `agent_step`. Accepted (design, Data and contracts).
- **Batch variants table:** the "Model" column shows the same model for `bedrock` and `bedrock-pipeline`, since the variants are already split by config name. It could use `model_key`.
- **Result: in this tiny sample (2 prompts × 1 repeat), the pipeline cost 1.4–2× more and scored lower.** A real comparison needs more repeats; `--repeats 3` over all six prompts is about $8.
