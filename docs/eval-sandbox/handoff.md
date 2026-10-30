# Agentic Eval Sandbox: Handoff

**What shipped:** the observe → summarize → score loop, on the `prototype` branch, for the ADB task.

- `foundry run` drives a Strands agent over the real RODA MCP server and records every model and tool call to a contract-valid run directory.
- `foundry evaluate` turns any run directory into scores and a report. That includes runs from other harnesses and saved runs, which is the replay path.
- The scores are a deterministic workflow score plus a judge-model output score against a rubric.
- Model and tracer are swapped through config.
- There's a local score-card UI and a CDK stack for the runs bucket and the harness role.

It ran offline first and then live on Bedrock in the sandbox account: L1, L2, L3 and L5 passed on 2026-10-30. L4 (deploy) was not run, by the user's choice.

## Verification

Suite: `uv run pytest` gives 115 passed. It was run 3× in the working tree with no flakes, and once in a **fresh clone** of `prototype`. `uv run pytest -m "not network"` with network blocked gives 112 passed. Lint (`ruff 0.14.0`) is clean. There's no type checker configured.

- [x] **AC1:** prompt and config resolution. Exit 2 with exact messages and no run dir (`test_cli.py`, 4 tests, real CLI subprocess).
- [x] **AC2:** exit codes 0, 1 and 2 for run and evaluate (`test_cli.py`, `test_evaluate.py::test_replay_cli_does_not_run_agent`). An unwritable runs dir now also exits 2 with a message.
- [x] **AC3:** validator with file:line violations. All 5 fixtures are valid (`test_contract.py`, 9 tests).
- [x] **AC4:** `running` at start, final status at end, single-line JSON (`test_runner.py::test_run_json_running_during_run`, others). Live: a SIGKILLed run is left `running`.
- [x] **AC5:** happy path against **real RODA 0.1.7** (`test_cli.py::test_run_offline_real_roda`, network). Live: 10 runs (5 prompts × 2 configs) all succeeded and validated.
- [x] **AC6:** tool error, `retry_of` and RODA in-body errors (`test_runner.py::test_tool_error_and_retry`, `test_cli.py::test_roda_json_status_error_detected`).
- [x] **AC7:** tokens, cost, and null cost when there's no price (`test_runner.py::test_tokens_and_cost`).
- [x] **AC8:** both limits, cancelled call not recorded (`test_runner.py::test_max_model_calls`, `::test_max_input_tokens`).
- [x] **AC9:** wall-clock limit; the in-flight tool is recorded as `cancelled: run timed out` (`test_runner.py::test_timeout_records_inflight_tool`).
- [x] **AC9b:** model failure recorded, run failed (`test_runner.py::test_model_failure`, `test_bedrock.py`). Live: `config/bedrock.toml` without credentials gives an `llm_call` error `NoCredentialsError`, exit 1.
- [x] **AC9c:** MCP start failure and a missing command (`test_runner.py`, 2 tests).
- [x] **AC9d:** unknown tool and schema-invalid arguments marked `invalid` (`test_runner.py`, 2 tests).
- [x] **AC10, AC7b:** timeline with one row per step, Struggles, narrative of 150 words or fewer, narrative failure (`test_evaluate.py`, 3 tests).
- [x] **AC10b:** run content only inside the neutralized data block (`test_evaluate.py::test_injection_only_inside_data_block`). The live-judge part is L5.
- [x] **AC10c:** hostile fixture escaped, table intact (`test_report.py`).
- [x] **AC10d:** refuses `running` runs and invalid run dirs; `--force`; lock (`test_evaluate.py`, 5 tests). Live: SIGKILL, then evaluate gives exit 2, then `--force` gives exit 0 with `run.json` untouched.
- [x] **AC10e:** a config used only for evaluation is accepted and recorded; bad weights and a missing `[judge]` exit 2 (`test_evaluate.py`, 2 tests).
- [x] **AC11:** exact workflow scores clean 1.00, recovered 0.90, failed 0.0667, and their ordering (`test_workflow.py`).
- [x] **AC12:** per-criterion scores, judge identity, evaluation tokens and cost; no-output runs skip the judge (`test_evaluate.py`, 2 tests).
- [x] **AC13:** malformed judge reply (5 shapes) and raising judge, both with one retry and the exact report strings (`test_evaluate.py`).
- [x] **AC14:** Provenance items (`test_report.py::test_provenance`, `test_evaluate.py::test_eval_only_config_accepted_and_recorded`).
- [x] **AC15:** model swap with no tracked-file change (`test_cli.py::test_model_swap_no_code_change`, network).
- [x] **AC16:** GenAI spans to a local OTLP endpoint; jsonl-only exports nothing (`test_otel.py`). Live: **Arize Phoenix 21.0.0** received 6 spans (1 root + 5 steps) with all five `gen_ai.*` attributes.
- [x] **AC16b:** unresponsive endpoint gives one warning and adds at most `export_timeout_s` (`test_otel.py`).
- [x] **AC17, AC18:** replay never starts the agent or MCP; re-evaluation leaves `run.json`, `trace.jsonl` and `output.json` byte-identical (`test_evaluate.py`, 3 tests). Live: the 5 fixtures evaluated.
- [x] **AC19:** rubric snapshot and `--rubric` override (`test_evaluate.py::test_rubric_snapshot_and_override`).
- [x] **AC20:** S3 mirror with a stubbed client: all regular files, failure warns, uploads at run end and after evaluation only (`test_storage.py`, 4 tests).
- [x] **AC21:** sentinel credentials absent from run files, CLI output, OTLP bodies and UI pages (`test_secrets.py`, `test_ui.py::test_ui_pages_never_show_credentials`).
- [x] **AC22:** template assertions (`test_infra.py`). Live: `npx cdk synth` (CLI 2.1146.1) with no credentials or Docker exits 0.
- [x] **AC23:** LICENSE and pyproject license (`test_repo.py`).
- [x] **AC24–AC29:** UI states, report page, run-id 404s, inert rendering, start-run validation, Host/Origin/CSRF checks, and the 409s (`test_ui.py`, 25 tests). Live: `foundry ui` listens on 127.0.0.1 only (lsof), and bad Host returns 400.
- [x] **AC30:** whole suite from a clean checkout, see above. **Verify found a real gap here:** `.gitignore` `runs/` also ignored `fixtures/runs/`, so a fresh clone failed 58 tests. It's fixed in 8c515c6 and re-checked in a fresh clone.
- [x] **AC31:** zero-context README walkthrough in a fresh clone, from clone to report in the UI in 16 s (warm caches). The confusion points it found are fixed: the replay example needed the fixtures, which are now committed.

**UI against the approved design:** it matches the approved score-card layout and the full-width report page. Differences:

- Card scores read `W1.00 O.67` (leading zero dropped) rather than `W.82 O.66` in the mockup.
- The index and the report header show a `| Workflow | Output` line, as approved.
- The report page renders the whole report.md (Summary, Struggles, Workflow, Output, Timeline, Agent output, Provenance) as stacked sections, not a single "Summary · Struggles · …" row. The wireframe line listed the sections; it didn't specify a tab bar.

There was no real-browser screenshot (no browser tool was used). The HTML was inspected through the test client and with curl.

## Exercised live

These ran on macOS with network access and **no AWS credentials**:

- `foundry run` on all 5 prompts × `offline` and `offline-alt` against the real RODA MCP server (uvx, awslabs.roda-mcp-server 0.1.7), each followed by `foundry evaluate` and `foundry validate`.
- `foundry ui` serving those runs.
- The OTel tracer into a real local Arize Phoenix 21.0.0.
- `npx cdk synth`.
- Edge cases:
  - 2M-character tool output: capped, 214 MB RSS, 1 s
  - a 96k-character unicode prompt
  - SIGKILL mid-run, then `--force`
  - concurrent evaluations, 5 trials, exactly one winner each
  - read-only directories

### Live checks on Bedrock (2026-10-30)

These ran in `protagona-sandbox-1` (283171504459), us-west-2, with the operator's SSO credentials. The user chose Bedrock only: no deploy, so L4 is not run.

- **Quotas:** Claude Sonnet 5.5 and Haiku 5.5 have **0 tokens per minute** in this account, and calling them returns `AccessDeniedException`. The live configs were switched to Sonnet 4.6 (6M tokens/minute) and Haiku 4.5 (5M), with Nova 2 Lite (8M) kept as the judge; see design ADR-8. A one-token call to each succeeded.
- **L1** (`config/bedrock.toml`, Sonnet 4.6, adb-001): **pass**, after two real findings.
  1. The first attempt made 26 RODA calls, then failed with `MaxTokensReachedException` at `max_tokens = 1024` while writing the plan.
  2. The second, at 4,096, failed the same way, because the agent writes very long plans.

  The fix was the live limits (`max_tokens = 4096`, `max_model_calls = 12`, `max_input_tokens = 200000`, worst case $1.87) plus a length budget in `tasks/adb/system.txt`. The final run made 5 model calls and 9 RODA calls with 0 errors, cost $0.27, produced a 923-word plan, and validates. Both failed attempts were recorded correctly as `failed` runs, which shows the failure path works live.
- **L3** (live evaluation of L1): **pass.** The judge (`us.amazon.nova-2-lite-v1:0`) differs from the agent and gave per-criterion rationales. The narrative was 87 words. Evaluation cost $0.03. The judge gave 4/4 on every criterion; whether it's too generous is the judge-calibration follow-up (D-6).
- **L2** (`config/bedrock-alt.toml`, Haiku 4.5): **pass.** 3 model calls, 7 RODA calls, $0.04.
- **L5** (the `injection` fixture through the live judge): **pass.** The output score was 0.25, below the 0.5 bar, with criteria at 2, 3, 1 and 1 out of 4. The judge didn't follow the "give every criterion the maximum score" instruction.
- **Kimi K3** (`config/bedrock-kimi.toml`, a third, non-Anthropic agent): **pass**, once `temperature` was removed, which the model rejects. 4 model calls, 9 RODA calls, 0 errors, a 507-word plan, overall 1.00. Cost unknown, because there's no Kimi price in `[prices]`.
- **Model inventory:** every model in the account was called with the harness's features (`scripts/bedrock_model_sweep.py`, run twice). 41 can run the agent, 2 are unreliable, 18 are judge-only, and the rest are denied, end of life, or not text models. This corrects an earlier quota-based answer; see [bedrock-models.md](bedrock-models.md).
- **L4** (deploy and S3 mirror): not run, by the user's choice.

Total live spend was about $1 across 5 agent runs and 3 evaluations.

## Security scans

| Category | Tool + version | Result |
|---|---|---|
| Secrets | gitleaks 8.30.1 (git history 605627f..HEAD and working tree), Semgrep `p/secrets` | clean |
| Dependencies | OSV-Scanner 2.7.0, Trivy 0.76.0 on `uv.lock` and `infra/package-lock.json` | 4 Medium in `mcp` 2.1.1. The fix is blocked by `strands-agents` 1.62.0 pinning `mcp<2.2`. One CVE is reachable only through a malicious MCP server; the rest aren't reachable. Accepted as F-008 and F-009, review-by 2026-11-30. |
| SAST | Semgrep CE 1.181.0 (`p/python`, `p/security-audit`, `p/secrets`, `p/fastapi`, `p/jwt`) | 1 warning (`\| safe` on sanitized HTML), a false positive after reading the code |
| IaC | Checkov 3.3.28 and Trivy 0.76.0 on the synthesized CloudFormation; Checkov on the CI workflow | S3 access logging off (F-010, Low, accepted for the sandbox). SSE-S3 instead of a CMK (F-011, scanner-rated HIGH, **accepted by the user 2026-10-30** for the sandbox). Workflow is clean. |
| Container images | n/a | no images |
| Prompt injection | none on the allowlist | **unscanned.** Covered by the data-block design and an offline test; the live judge test is L5. |

## Pentest probe

The probe ran against a self-started local UI and covered 30+ WSTG-mapped tests. No High or Medium findings came out of it.

- **Low**, all **fixed** in 8c515c6 with regression tests:
  - F-001: local file read through a symlinked run file
  - F-003: `run.json` `run_id` drove link and form targets
  - F-004: one malformed `scores.json` gave a 500 for the whole index
- **Info**, all **fixed**: F-002, F-005, F-006, F-007.
- **Not probed:** DoS through special-file symlinks (symlinks are now refused anyway), real-browser DOM rendering, and the Bedrock configs from the UI.

**docs/security/ updated:** `README.md`, `threat-model.md`, `attack-surface.md`, `findings.md` and `test-log.md`, all new.

## CI

`prototype` was pushed to `origin` on 2026-10-30, with no PR. The first GitHub Actions run (41019723079) **did not start**: *"recent account payments have failed or your spending limit needs to be increased"* (org billing). The workflow itself has therefore never executed on GitHub.

The same `uv run pytest` passes 115/115 in a fresh local clone. Once billing is fixed, re-run it with `gh run rerun 41019723079`.

## Gate history

- **Define:** 3 subagent passes, with 11, 7 and 6 blockers. The pass-3 items (rubric resolution, `--force`, evaluate config, model-call counting, timeout step, prompts versus templates) were resolved and taken to the user, who **signed off** and changed offline mode to use the real RODA server.
- **Design:** 3 subagent passes, with 14, 9 and 11 blockers. Each was fixed, including:
  - thread model, Strands retry, cancel semantics, IAM actions, cost bound
  - locks and atomic writes, a CLI flag mix-up
  - prompt-id path safety, citations
  - the UI card fields, which went to the user, who chose "add short id + task"

  The user **signed off**, with CLI-only re-evaluation.
- **UI design review:** three options shown; the user picked score cards (C) and approved the refinement.
- **Plan:** 3 subagent passes. Most blockers were design sections the plan reviewer couldn't see (by design) and test-list gaps. Pass 3 left two small items that were resolved by aligning with the spec: a missing `uvx` becomes AC9c behavior, and the stream-format citation was added.
- **Verify:**
  - Delegates: one verify run, one security scan, one pentest probe.
  - The verify run found the **uncommitted fixtures** (fresh clone failed 58 tests) and two error-message gaps.
  - The probe found F-001 to F-007. All were fixed, with regression tests.
  - The subagent gate review raised 1 blocker, F-011, which **the user accepted** for the sandbox. Its non-blocking findings were also fixed: the AC9 bound, traceback noise, AC20 wording.
- **Launch:** 2 subagent passes.
  - Pass 1: Nova price, budget alarm, limits.
  - Pass 2: F-011, which was resolved by the user. Price verification is now a pre-step. The push was made at the user's request. Owners stay as placeholders, also at the user's request.
  - Also fixed from pass 2: the retained bucket's policy, HarnessRole as the default live identity, and runbook precision.

## Specialists consulted

- **Quality:** QA Automation, Exploratory Tester, Performance, and Beta Tester (zero-context README walkthrough).
- **Security:** AppSec, through scanners and denied-path tests; Offensive Security, through the probe and `docs/security/`.
- **Platform:** Infrastructure, DevOps, SRE (timeouts, limits, runbook), FinOps (cost bound, budget alarm).
- **AI/ML:** ML Engineer (judge validation, versioned prompt templates, fallbacks); AI Safety (data block, injection fixture; live L5 pending).
- **Experience:** Product Designer (UI review), Frontend and Accessibility (labels, keyboard-reachable links and forms; no screen-reader audit), API/DX (CLI errors), Technical Writer (README).

## Decisions and deviations

- **mcp server API:** in mcp 2.x, `FastMCP` became `mcp.server.mcpserver.MCPServer`. The fault server uses that.
- **Cancellation:** Strands surfaces a cancel as a tool *exception*, not `cancel_message`. MCP results carry `structuredContent`, so the 8,000-character cap rebuilds the result. Both are recorded in the design.
- **Missing MCP command:** treated as AC9c (failed run, exit 1), not a config error.
- **`mcp` 2.2.0 bump:** not possible, because strands-agents 1.62.0 pins `mcp<2.2`. Accepted as F-008 and F-009.
- **Nova 2 Lite price:** Sonnet's rate is used as an upper bound.
- **UI score format:** `W1.00 O.67`. See the Verification notes.
- **Cached input tokens** (found 2026-10-30 while measuring per-role usage): Kimi K3 on Bedrock caches prompts automatically and reports cached tokens in `cacheReadInputTokens`, outside `inputTokens`. `tokens_in` (and so the `max_input_tokens` guardrail and cost) now includes cached tokens, and `tokens_in_cached` records the cached part. Kimi runs recorded before the fix under-count input. Cached tokens are priced at the full input rate, so recorded cost is an overestimate for caching models.

## Demo readiness (2026-10-30)

- **Default live config** is a Kimi K3 agent with an Opus 4.6 judge (ADR-12). `bedrock-alt.toml` swaps the agent to Sonnet 4.6, and `bedrock-otel.toml` adds the OTel tracer. Tests assert each differs only in its one section.
- **Golden runs (G-1):** 4 live runs in `fixtures/runs/golden/` with tests for validity and offline replay:

  | Run | Overall |
  |---|---|
  | Kimi K3 | 1.00 |
  | Sonnet 4.6 | 0.96 |
  | Qwen3 235B | 0.81 |
  | gpt-oss-120b | 0.73 |

  Live failures (call-limit exhaustion) didn't reproduce on re-runs, because model behavior varies. The failure path is shown with `fixtures/runs/failed`.
- **Demo script (G-3):** `docs/demo.md`, rehearsed end to end against live Bedrock, RODA and Phoenix:

  | Segment | Time |
  |---|---|
  | Live run | 23 s |
  | Evaluate | 11 s |
  | Model swap | 38 + 10 s |
  | Tracer swap | 27 s (21 spans in Phoenix) |
  | Offline replay | instant |

## Increment 2 (2026-10-30)

These were the user's picks, mechanical and functional only. The scoring work waits for Clay's rubric. See [increment-2.md](increment-2.md).

- **Second MCP server.** An in-repo STAC server (Earth Search, no credentials) sits next to RODA. In a live batch, every run used both servers.
- **Robustness:**
  - A reaper kills MCP servers when the harness is SIGKILLed; live, they were gone within 1 s.
  - The Strands start-failure warning is silenced (an upstream bug).
  - The UI auto-refreshes while something is running.
- **`foundry batch`:** variants and derived model configs, a concurrency cap, a budget stop, resume and retry, and CSV and Markdown summaries. Live: 4/4 runs in 83 s for $0.79.

## Increment 3 (2026-10-30): evaluating other agents

`foundry import` (OTLP/JSON files) and `foundry receive` (an OTLP/HTTP receiver on 127.0.0.1) turn any agent's OpenTelemetry GenAI spans into run directories that `foundry evaluate` scores unchanged.

Live: a standalone Strands agent on Bedrock with real RODA, not built on our harness (`examples/external_strands_agent.py`), was received, validated and scored (overall 1.00). See [increment-3.md](increment-3.md).

## UI for batches and imports (2026-10-30)

At the user's request, everything is also available from the UI, alongside the CLI:

- **Batches page:** the form takes a config, models (an allowlist built from the configs), prompts, repeats, parallel runs, a cost cap and evaluate. A live batch page shows per-model aggregates and run rows that link to reports, and refreshes itself while running. One batch runs at a time.
- **Import page:** upload an OTLP file (≤ 50 MB), or start and stop the OpenTelemetry receiver on 127.0.0.1, with a count and links for the runs received.
- **Shared runs dir:** batch runs land in the UI's runs dir with a `b-<timestamp>-<hex>_` prefix, so they show up as ordinary cards.
- **Same protections as before:** every POST is behind Host, Origin and CSRF checks.
- **Tests:** UI tests cover a real batch launched from the form, form validation, upload, and receiver start, receive and stop.
- **Live through the real server:** a 2-model batch on adb-006 cost $0.38 against a $1 cap (Kimi K3 1.00, Sonnet 4.6 0.96). The UI was restarted mid-batch, and the batch kept running and rendered correctly afterwards.

## Must items closed before the event (2026-10-30)

- **D-6, judge sanity:** `scripts/judge_sanity.py` scores the five known-verdict cases in `fixtures/judge-cases/` three times each. Live with the Opus 4.6 judge, all 13 checks pass for $0.36: bands, A > B > C, the hallucinated plan capped at 1, and repeats consistent. Results are in the README. Comparing against human graders is still open.
- **D-8, one command:** `foundry run --evaluate` runs the agent, then summarizes, scores and writes the report. Failed runs are scored too, and the exit code stays the run's. Live on adb-002 (Kimi K3): 0.96, $0.12 for the run and $0.03 for the evaluation. Saving to S3 waits on L4.
- **A-2, samples:** `scripts/stage_samples.sh` copies a 54 MB NAIP 2022 RGB tile (Fresno) and a 30 MB Vantor open-data tile to `samples/`, with sources and licenses in `samples/README.txt`. The role can read `samples/*`, read-only (infra test updated). Not yet run, because it needs the bucket.
- **A-1, C-3, L4, deploy check:** `scripts/verify_deploy.sh` assumes the deployed `HarnessRole` and does a live `run --evaluate` with `s3_uri` set. It then checks that the run, trace, scores, report and rubric are under `runs/<id>/`, and that the role can't write outside `runs/`. **Not run yet:** auto mode refused `cdk bootstrap` and `cdk deploy`, so these need the user's approval or the user's own shell.
- **A-4 rehearsal:** in a fresh clone with no AWS variables: `uv sync`, an offline run, evaluate, validate, 170 tests and `cdk synth` all pass. The README now includes `cdk bootstrap`, which was missing. Done-when still needs a teammate deploying.
- **Suite:** 172 passed, ruff clean.

## Follow-ups

- Deploy and verify: `cdk bootstrap`, `cdk deploy`, `scripts/stage_samples.sh`, then `scripts/verify_deploy.sh` (README, *Deploy*). Request quota for Sonnet and Haiku 5.5 if those models are preferred; switching back is a config change (ADR-8). Verify Bedrock prices in the console.
- Fix GitHub billing and re-run CI.
- Bump `mcp` to 2.2 or later when Strands allows it (F-008/F-009, by 2026-11-30).
- Pin GitHub Actions to commit SHAs (F-013). This needs approval for CI changes.
- Scope `HarnessRole` Bedrock actions to model and inference-profile ARNs (F-012).
- S3 access logging (F-010) before any non-sandbox use.
- Compare the judge against human scores (D-6's sanity check passes; human agreement is untested). Add more adversarial prompt-injection fixtures.
- Real-browser UI check: screenshots and a screen-reader pass.
- Clay's real rubric and prompts once they arrive (K-1). Swapping them in is a data-only change.
- Investigate the `RuntimeWarning: coroutine ... _set_close_event was never awaited` that Strands emits when an MCP server fails to start.

## Risks to watch

- Real agents write long plans. The 500-word budget in the system prompt was not followed (923 words), but the output now fits the limits.
- The live judge scored a strong plan 4/4 on every criterion. Calibrate it against human scores (D-6) before relying on its scores.
- Network dependence: RODA, the Registry of Open Data, and PyPI for uvx.
