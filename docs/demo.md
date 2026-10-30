# Demo script (G-3)

This runs in about **10 minutes**, with about 2 minutes of it waiting on models. Each segment proves one of the spec's success signals.

It was rehearsed on 2026-10-30 end to end against live Bedrock, RODA and Phoenix. The timings below are from that rehearsal.

## Pre-flight (15 minutes before)

1. `aws sso login --sso-session protagona`, then:
   ```bash
   export AWS_PROFILE=protagona-sandbox-1 AWS_REGION=us-west-2
   aws sts get-caller-identity     # account 283171504459
   ```
2. From the repo root, warm the caches so the first live run isn't slow: `uv sync && uv run foundry run --config config/offline.toml --prompt adb-001 --runs-dir /tmp/warm`. This also pre-fetches the RODA MCP server through uvx.
3. Start Phoenix in its own terminal: `uvx --from arize-phoenix phoenix serve`. It's ready when `curl -s localhost:6006/healthz` returns 200, about 10 s.
4. Use a clean runs directory for the demo: `export DEMO=/tmp/demo-runs && rm -rf $DEMO`.
5. Browser tabs:
   - `http://127.0.0.1:8765` (start the UI in step 6)
   - `http://localhost:6006` (Phoenix)
   - [docs/eval-sandbox/bedrock-models.md](eval-sandbox/bedrock-models.md), for questions
6. Start the UI in a third terminal: `uv run foundry ui --runs-dir $DEMO`.

## Run of show

| # | Say | Do | Expect (rehearsal timing) |
|---|---|---|---|
| 1 | "An agent plans a dataset build by calling real geospatial tools. Every step is recorded." | `uv run foundry run --config config/bedrock.toml --prompt adb-002 --runs-dir $DEMO` | Prints the run directory and exits 0 (~25 s). Show `trace.jsonl` scrolling: model calls with tokens and cost, and RODA tool calls. |
| 2 | "Now we score it on two axes: workflow from the trace, output from a judge model." | `uv run foundry evaluate $DEMO/<run id> --config config/bedrock.toml` | `overall … \| workflow … \| output …` (~11 s). Open the card in the UI, then the report: summary, struggles, per-criterion rationales from Opus 4.6, timeline, provenance. |
| 3 | "Swapping the model is a config change, not a code change." | `diff config/bedrock.toml config/bedrock-alt.toml`, then the run and evaluate commands from steps 1–2 with `config/bedrock-alt.toml` | The diff is a single line (Kimi K3 → Sonnet 4.6). Run ~40 s, evaluate ~10 s. A second card appears with its own scores. |
| 4 | "Same for the tracer: add OpenTelemetry and the same steps land in any OTLP backend." | `diff config/bedrock.toml config/bedrock-otel.toml`, then `uv run foundry run --config config/bedrock-otel.toml --prompt adb-002 --runs-dir $DEMO` | Run ~27 s. In Phoenix, open the new trace: `invoke_agent foundry` with `chat` and `execute_tool` child spans carrying `gen_ai.*` attributes (the rehearsal had 21 spans). |
| 5 | "If the live agent is flaky, we replay recorded runs. Scoring never re-runs the agent." | `cp -r fixtures/runs/golden/golden-kimi-k3-adb-001 /tmp/replay && uv run foundry evaluate /tmp/replay --config config/offline.toml` | Instant, and needs no AWS. `trace.jsonl` is unchanged and a new report is written. |
| 6 | "And the scores separate models." | `uv run foundry ui --runs-dir fixtures/runs/golden --port 8766`, opened in a second tab | Four cards: Kimi K3 1.00, Sonnet 4.6 0.96, Qwen3 0.81 (used 11 of 12 model calls), gpt-oss-120b 0.73. |

Close with: **"Evaluation is the product."** The agent, the model and the tracer are all swappable; the run directory is the contract.

## Talking points for questions

- **Why trust the judge?** We calibrated 7 candidate judges on known-good, vague, fabricated and prompt-injection plans. Nova 2 Lite rated a one-line plan above a real one, and Mistral believed a made-up dataset. Opus 4.6 and Kimi K3 caught both for the right reason. ([bedrock-models.md](eval-sandbox/bedrock-models.md#judge-calibration-2026-10-30))
- **Why Kimi as the agent and Opus as the judge?** The agent uses about 15× the judge's tokens. Kimi K3 is 40% cheaper per token and caches automatically, so it takes the heavy role. A run plus its evaluation costs about $0.16, against about $0.38 with the roles reversed. (Design ADR-11)
- **Which models work?** We called every model in the account with the harness's exact features: 41 can drive the agent. Quotas lied: GPT-5.x and 6.x are access-denied, and Llama can't stream tool calls.
- **Cost guardrails:** at most 12 model calls, 200k input tokens and 300 s per run, so the worst case is about $1.83.
- **Bring your own task:** Clay's rubric and prompts are data files under `tasks/`; no code changes are needed.

## Fallbacks

| If… | Then |
|---|---|
| Bedrock or SSO fails (expired token, throttling) | Skip steps 1–4 live. Show the golden runs in the UI (step 6), open a golden report, and do the replay (step 5). Everything still works offline. |
| The RODA or registry network is down | Same as above. Also `config/offline.toml` runs the scripted agent, but it needs RODA too, so use the golden runs. |
| A live run fails or times out | That's a feature: open its card. The failure, the limit hit and the struggles are all in the report. Then continue. |
| Phoenix won't start | Show `trace.jsonl` and the `run.json` `"tracer": "jsonl+otel"` field. Explain that any OTLP endpoint works (Langfuse, Honeycomb, etc.). |
| The UI port is taken | Use `--port 8777`. |

## After the demo

`rm -rf $DEMO /tmp/replay`, and stop Phoenix and the UI with Ctrl-C.
