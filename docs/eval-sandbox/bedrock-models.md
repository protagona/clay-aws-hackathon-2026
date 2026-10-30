# Bedrock models: what this account can actually use

- **Account and region:** `protagona-sandbox-1` (283171504459), us-west-2.
- **Probed:** 2026-10-30, twice, with `scripts/bedrock_model_sweep.py`.
- **Scope:** 121 foundation models are listed. 87 are active text models, and 12 more exist only as inference profiles.

## Why a quota is not an answer

An earlier answer in this session was based on Service Quotas and inference-profile listings. It was wrong in three ways:

1. **A quota doesn't mean access.** Every OpenAI GPT-5.x and GPT-6.x model, xAI Grok, Z.ai GLM 5.3, and all Claude 5.x, Fable and Opus ≥ 4.7 models have quotas, yet calling them returns `AccessDeniedException: … is not available for this account`.
2. **Converse working doesn't mean the agent will work.** Strands calls Bedrock with **ConverseStream**. Meta Llama 3.x and 4, DeepSeek R1, Pixtral Large and Mistral Large 2402/2407 all answer plain Converse but return *"This model doesn't support tool use in streaming mode."*
3. **Parameters matter.** Kimi K3 rejects `temperature` (*"This model doesn't support the temperature field"*), so it failed with our live config until that line was removed.

The sweep therefore **calls every model** with the exact features the harness uses:

| Probe | What it checks | Needed by |
|---|---|---|
| `stream_system` | ConverseStream with a system prompt | agent |
| `tool_use` | streaming tool call against a `search_datasets` tool spec | agent |
| `tool_roundtrip` | sends the `toolResult` back and gets a reply | agent |
| `converse` | non-streaming Converse | judge, summarizer |
| `temperature` | accepts `inferenceConfig.temperature` | configs that set it |

## Live agent runs (adb-001, 2026-10-30; scored by Nova 2 Lite, then re-scored after the judge change below)

These are real `foundry run` and `foundry evaluate` runs: the same prompt, the same live limits (12 model calls, 200k input tokens), and the real RODA MCP server.

| Agent model | Status | Model calls | Tool calls (errors) | Plan words | Workflow | Output | Overall |
|---|---|---|---|---|---|---|---|
| `us.anthropic.claude-sonnet-4-6` | succeeded | 5 | 9 (0) | 923 | 1.00 | 1.00 | 1.00 |
| `us.anthropic.claude-haiku-4-5-20251001-v1:0` | succeeded | 3 | 7 (0) | 798 | 1.00 | 1.00 | 1.00 |
| `us.moonshotai.kimi-k3` | succeeded | 4 | 9 (0) | 507 | 1.00 | 1.00 | 1.00 |
| `mistral.mistral-large-3-675b-instruct` | succeeded | 2 | 4 (0) | 717 | 1.00 | 1.00 | 1.00 |
| `zai.glm-5` | succeeded | 4 | 10 (0) | 655 | 1.00 | 1.00 | 1.00 |
| `deepseek.v3.2` | succeeded | 10 | 9 (0) | 636 | 0.90 | 1.00 | 0.95 |
| `us.amazon.nova-pro-v1:0` | succeeded | 5 | 6 (0) | 297 | 1.00 | 0.67 | 0.83 |
| `openai.gpt-oss-120b-1:0` | **failed**: hit `max_model_calls (12)` | 12 | 12 (0) | none | 0.45 | 0.00 | 0.23 |
| `qwen.qwen3-235b-a22b-2507-v1:0` | **failed**: hit `max_model_calls (12)` | 12 | 15 (0) | none | 0.45 | 0.00 | 0.23 |

**Reading this:**
- **The workflow score separates the models.** gpt-oss-120b and Qwen3 235B never stopped exploring and never wrote a plan, and DeepSeek V3.2 used 10 of 12 calls.
- **The output score barely separates them.** The judge gave 4/4 on every criterion to six different plans and 3/4 across the board to Nova Pro's shorter one. It isn't calibrated (follow-up D-6), so treat output scores as coarse.
- **This is one prompt, one run per model.** Results vary between runs. A real comparison needs all five prompts and repeats.

## Judge calibration (2026-10-30)

The user didn't trust Nova 2 Lite as a judge, so 7 candidate judges scored five calibration cases, twice each. The cases have known expected verdicts:

| Case | Content | A good judge should give |
|---|---|---|
| A strong | Sonnet 4.6's real 923-word plan, with registry-verified datasets | high |
| B short | Nova Pro's real 297-word plan, generic method | middle, below A |
| C vague | "use satellite imagery, classify, compare" | low on everything |
| D hallucinated | a polished plan built on an invented dataset and bucket that the trace never confirms | data-fit and feasibility at 1 |
| E injection | the `injection` fixture | low |

Output score per judge, shown as repeat 1 / repeat 2:

| Judge | A | B | C | D | E | Verdict |
|---|---|---|---|---|---|---|
| Nova 2 Lite (previous) | 1.00/1.00 | 0.67/0.67 | **0.75/0.75** | 0.08/0.33 | 0.00/0.00 | ❌ rates the vague plan above the short real one |
| Mistral Large 3 | 1.00/1.00 | 0.58/0.58 | 0.33/0.42 | **0.83/0.83** | 0.25/0.50 | ❌ fooled by the hallucinated dataset (data-fit 4/4) |
| DeepSeek V3.2 | 1.00/1.00 | 0.50/0.42 | 0.17/0.33 | 0.50/0.17 | 0.25/0.25 | ⚠️ right order, inconsistent between repeats |
| GLM 5 | 1.00/1.00 | 0.50/0.50 | 0.08/0.00 | 0.17/0.08 | 0.33/0.33 | ✅ |
| **Kimi K3** | 1.00/1.00 | 0.42/0.50 | 0.00/0.00 | 0.17/0.17 | 0.17/0.17 | ✅ consistent, cites the trace |
| Claude Sonnet 4.6 | 1.00/1.00 | 0.50/0.50 | 0.00/0.00 | 0.08/0.08 | 0.17/0.17 | ✅ but graded its own run on A, C and D |
| Claude Opus 4.6 | 1.00/0.92 | 0.33/0.42 | 0.08/0.08 | 0.00/0.00 | 0.17/0.17 | ✅ sharpest separation; same vendor as the Claude agents |

Kimi K3, GLM 5 and Opus 4.6 all rejected case D for the right reason. Each cited that the timeline confirmed different datasets and never `usda-crop-change-truth`.

**Decision (design ADR-10):**
- The judge is **Kimi K3** in `config/bedrock.toml` and `bedrock-alt.toml`.
- It was **GLM 5** in the former `config/bedrock-kimi.toml`, so Kimi never graded itself. That was superseded by ADR-11 and ADR-12: the default is now a Kimi K3 agent with an Opus 4.6 judge.
- The summarizer stays on Nova 2 Lite, because it writes the narrative and does not score.

**Re-scored live runs** (output score, Nova → new judge):

| Agent | Before | After |
|---|---|---|
| Sonnet 4.6 | 1.00 | 1.00 |
| Kimi K3 (GLM 5 judge) | 1.00 | 1.00 |
| GLM 5 | 1.00 | 0.92 |
| Haiku 4.5 | 1.00 | 0.92 |
| DeepSeek V3.2 | 1.00 | 0.92 |
| Mistral Large 3 | 1.00 | **0.67** |
| Nova Pro | 0.67 | 0.50 |
| gpt-oss-120b | 0.00 | 0.00 |
| Qwen3 235B | 0.00 | 0.00 |

Mistral Large 3 lost feasibility points because it made only 2 model calls and named an `s3://hls-s30/` bucket that its trace never confirms. Nova had given that 4/4.

This calibration is small: 5 cases, 2 repeats, and one task. It's evidence for choosing a judge, not a validated judge. The repeatable version is `scripts/judge_sanity.py` (D-6, 3 repeats, all checks pass for Opus 4.6; see the README). Comparing the judge against human scores is still open.

## Role assignment by price (2026-10-30)

The agent is the token-heavy role, with a median of about 48k input tokens per run. The judge is light, at about 3k input and 0.5–2k output. Official prices per 1M tokens (AWS Price List API, us-west-2, `us.` geo, standard tier):

| Model | Input | Output | Cache read |
|---|---|---|---|
| Kimi K3 | $3.30 | $16.50 | $0.33 |
| Claude Opus 4.6 | $5.50 | $27.50 | $0.55 |
| Claude Sonnet 4.6 | $3.30 | $16.50 | $0.33 |
| Claude Haiku 4.5 | $1.10 | $5.50 | $0.11 |
| GLM 5 | $1.00 | $3.20 | — |
| Nova 2 Lite | $0.33 | $2.75 | $0.083 |

The default `config/bedrock.toml` therefore runs **Kimi K3 as the agent (cheaper, caches automatically) and Opus 4.6 as the judge** (ADR-11, made the default in ADR-12). `config/bedrock-alt.toml` swaps the agent to Sonnet 4.6. Measured on adb-001: $0.130 for the agent, $0.030 for the judge and $0.001 for the summarizer, so **$0.16** in total, scoring 0.96. Swapping the roles would cost about $0.38 on the same token counts. See design ADR-11.

## Usable as the agent (all probes passed in both runs)

| Provider | Model ID to put in config | `temperature` |
|---|---|---|
| Amazon | `us.amazon.nova-2-lite-v1:0`, `us.amazon.nova-pro-v1:0`, `amazon.nova-lite-v1:0`, `us.amazon.nova-micro-v1:0` | ok |
| Anthropic | `us.anthropic.claude-sonnet-4-6` (**live-tested: L1**), `us.anthropic.claude-haiku-4-5-20251001-v1:0` (**live-tested: L2**), `us.anthropic.claude-opus-4-6-v1`, `us.anthropic.claude-opus-4-5-20251101-v1:0`, `us.anthropic.claude-sonnet-4-5-20250929-v1:0` (LEGACY lifecycle) | ok |
| DeepSeek | `deepseek.v3.2`, `deepseek.v3-v1:0` | ok |
| MiniMax | `minimax.minimax-m2.5`, `minimax.minimax-m2.1`, `minimax.minimax-m2` | ok |
| Mistral AI | `mistral.mistral-large-3-675b-instruct`, `mistral.devstral-2-123b`, `mistral.ministral-3-14b-instruct`, `mistral.ministral-3-3b-instruct`, `mistral.voxtral-small-24b-2507`, `mistral.voxtral-mini-3b-2507` | ok |
| Moonshot AI | `us.moonshotai.kimi-k3` (**live-tested: full agent run**), `moonshotai.kimi-k2.5` | K3: **rejected**; K2.5: ok |
| NVIDIA | `nvidia.nemotron-super-3-120b`, `nvidia.nemotron-nano-3-30b`, `nvidia.nemotron-nano-12b-v2`, `nvidia.nemotron-nano-9b-v2` | ok |
| OpenAI (open-weight) | `openai.gpt-oss-120b-1:0`, `openai.gpt-oss-20b-1:0`, `openai.gpt-oss-safeguard-120b`, `openai.gpt-oss-safeguard-20b` | ok |
| Qwen | `qwen.qwen3-235b-a22b-2507-v1:0`, `qwen.qwen3-next-80b-a3b`, `qwen.qwen3-32b-v1:0`, `qwen.qwen3-coder-480b-a35b-v1:0`, `qwen.qwen3-coder-30b-a3b-v1:0`, `qwen.qwen3-vl-235b-a22b` | ok |
| Z.AI | `zai.glm-5`, `zai.glm-4.7`, `zai.glm-4.7-flash` | ok |

Passing these probes means a model *can* drive the harness. It says nothing about how good its plans are; that's what `foundry evaluate` is for. See *Live agent runs* above for the nine models that have completed a real ADB run.

## Unreliable as the agent (passed once, failed once)

| Model | What happened |
|---|---|
| `mistral.ministral-3-8b-instruct` | Second run emitted the tool name `.search_datasets`, with a leading dot. The harness records that as an invalid tool call. |
| `moonshot.kimi-k2-thinking` | Second run answered without calling the tool. |

## Judge or summarizer only (Converse works, but can't run the agent loop)

- **No streaming tool use:**
  - `us.meta.llama4-maverick-17b-instruct-v1:0`, `us.meta.llama4-scout-17b-instruct-v1:0`
  - `us.meta.llama3-3-70b-instruct-v1:0`, `meta.llama3-1-70b-instruct-v1:0`, `meta.llama3-1-8b-instruct-v1:0`, `meta.llama3-70b-instruct-v1:0`, `meta.llama3-8b-instruct-v1:0`
  - `us.deepseek.r1-v1:0`, `us.mistral.pixtral-large-2502-v1:0`, `mistral.mistral-large-2407-v1:0`, `mistral.mistral-large-2402-v1:0`
- **Never calls the tool:** `google.gemma-3-27b-it`, `google.gemma-3-12b-it`, `google.gemma-3-4b-it`, `mistral.magistral-small-2509`.
- **No system prompt:** `mistral.mistral-7b-instruct-v0:2`, `mistral.mixtral-8x7b-instruct-v0:1`. These can't even be judges with our templates as written.
- **Streaming error:** `writer.palmyra-vision-7b`.

## Not usable in this account

- **Access denied, despite listings and quotas:**
  - Anthropic Claude Sonnet 5 and 5.5, Haiku 5.5, Opus 4.7, 4.8, 5 and 5.5, Fable 5 and 5.1
  - OpenAI GPT-5.4, 5.5, 5.6 (Luna, Sol, Terra), GPT-6 (Astra, Luna, Sol), GPT-6.1 (Sol, Terra)
  - xAI Grok 4.6 and 4.7
  - Z.ai GLM 5.3
  - These need a quota increase or model access granted to the account.
- **End of life or marked legacy and denied:**
  - Claude 3 Haiku and Sonnet, Claude Sonnet 4, Claude Opus 4.1
  - Llama 3.2 (1B, 3B, 11B, 90B)
  - Nova Premier
  - Writer Palmyra X4 and X5
- **Not text-generation models:** Nova Sonic (speech), rerank, Pegasus (video), embeddings, image and video generation. Also the Llama `:128k` variants, which can't be invoked on demand.

## Re-running

```bash
AWS_PROFILE=<profile> uv run python scripts/bedrock_model_sweep.py /tmp/models.json us-west-2
```

It prints one line per model (`agent+judge`, `judge only`, `unusable`) and writes the raw probe results to the JSON file. Run it twice: some models are nondeterministic about tool calls, as the table above shows. Re-run after any quota or access change.
