# Agentic Eval Sandbox: Handoff

**What shipped:** the observe → summarize → score loop, on the `prototype` branch, for the ADB task.

- `foundry run` drives a Strands agent over the real RODA MCP server and records every model and tool call to a contract-valid run directory.
- `foundry evaluate` turns any run directory into scores and a report. That includes runs from other harnesses and saved runs, which is the replay path.
- The scores are a deterministic workflow score plus a judge-model output score against a rubric.
- Model and tracer are swapped through config.
- There's a local score-card UI and a CDK stack for the runs bucket and the harness role.

Everything runs offline: there are no AWS credentials yet, so the agent, the judge and the summarizer are faked. Live Bedrock checks L1–L5 are pending.

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

**Not reachable:** Bedrock and the `cdk deploy` (L1–L5), because there are no AWS credentials.

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
