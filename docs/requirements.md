# Requirements (plain language)

Distilled from the organizer docs: [Vision](Clay-Foundry-Vision-Hackathon.md) and [Technical](Clay-Foundry-Technical-Hackathon.md).

## The one-sentence goal

Prove that an agentic geospatial workflow can be **observed, summarized, and scored** end to end. *"Evaluating the agent is the product. The deliverable is the observe-summarize-score loop, not a one-off dataset."*

## Must have

Requirements are from the docs (Vision: MVP Success Criteria and Definition of Done; Technical: Hackathon Approach). The right-hand column is **our own** acceptance check.

| # | Requirement | Our check |
|---|-------------|-----------|
| M1 | An agent on **Amazon Bedrock** performs a chosen Clay task (the ADB eval or caption generation) by calling geospatial tools over **MCP**, using public or synthetic input data. Start with the RODA MCP server. | A run completes and its tool calls hit a real MCP server. |
| M2 | **Record** every step: tool calls, arguments, order, failures/retries, latency, tokens/cost. | A trace file exists for the run and matches the [trace contract](trace-contract.md). |
| M3 | **Summarize** the trace into a readable account of what the agent did and where it struggled. | Someone who didn't build it can read the summary and say what the agent did and where it struggled. |
| M4 | **Score** on two axes: **workflow** (from the trace: right tools, sensible order, error recovery, steps/cost) and **output** (vs. a rubric or reference examples). | Both scores appear, each with a rationale. |
| M5 | A **per-run evaluation report** combining summary + scores. | One file/page per run. |
| M6 | **Model and tracing tool are both swappable** behind an abstraction. *Demonstrated*, not just designed. | Demo: switch the model **and** the tracer through config, with no code change. |
| M7 | **Replay**: if the live agent fails, recorded traces can still be scored into a report. | Report generated from a saved run without running the agent. |
| M8 | **Infrastructure as Code**, **open source on GitHub** (from the organizer email), **Apache-2.0**. Every scored run keeps its trace, inputs, and the rubric or reference it was judged against (provenance). | `deploy` from a clean checkout works; the repo is public with a LICENSE; Clay can access it after the event. |

## Should have

- **Minimal UI**: a thin view to launch a run and read its report (FastAPI is the docs' preferred backing API).

## Could have (only after every Must is green)

- A **second task** (caption generation for Clay v2 is the natural one).
- **Multi-agent** traces (planner / executor / checker).
- **Sensitivity analysis**: vary prompt, model, or tools; compare scores across runs.
- **Dynamic tool discovery** across Clay's ~99 MCP servers.
- A richer trace dashboard.

## Should NOT have

- Training the Clay model, or running Clay's whole eval backlog.
- Private, customer, or sensitive data — **public or synthetic only** (NAIP, Vantor open data, LGND embeddings, synthetic prompts and captions).
- Multi-account or partner-account deployment; continental-scale runs.
- Heavy infrastructure such as SageMaker HyperPod / Trainium clusters, or a full Snowflake / Iceberg / Athena / Managed Grafana stack. A **lightweight** trace and metadata store stands in for these.
- A hard-wired model or tracing vendor.
- Grading the output alone without looking at the trace.
- Starting the open third track without Clay's sign-off.

## Heads-up from the organizers

*"The use case may evolve during the week - stay flexible. The faster your team aligns on a problem, the more time you'll have to build."* This is why the plan keeps the evaluation side independent of which task or harness we end up with.

## Tasks to choose from

Each evaluated task needs three things: a **known task**, **known input**, and a **reference to score against**.

1. **ADB agentic eval**: *start here.*
   - The agent performs an ADB-style "how to use AI / build datasets" task.
   - We bring the synthetic prompts.
   - It is graded against Clay's rubric.
   - The input data, the **harness(es) under test** and the rubric are listed as coming **from Clay**. We may be evaluating Clay's agent and not only our own, so our harness must be replaceable.
2. **Caption generation** for Clay v2:
   - Input is NAIP imagery plus Vantor samples.
   - The agent writes captions that support retrieval ("find railroads"). LGND embeddings are available for retrieval.
   - The LGND embeddings cover Sentinel-2, not NAIP, but Clay publishes separate NAIP embeddings (`clay-v1-5-naip-2`; see [Other Clay embeddings](#other-clay-embeddings) below). Confirm with Clay which set the retrieval eval uses.
   - It is graded against Clay's example captions and retrieval eval set.
3. **Open track**: a third task from Clay's eval lists, or a dataset-build target (e.g., a flooding or building-value layer). It is optional and only with Clay's explicit confirmation.

## Known constraints

- **Region and billing:**
  - NAIP is **Requester-Pays in us-west-2**, and the docs say to "account for region and billing".
  - *Our call:* deploy in **us-west-2**, next to the data.
  - The docs' preferred pattern is to **read data at the source via MCP**. Pre-stage a tiny sample when that's "faster/cheaper (Requester-Pays)". We pre-stage as a fallback (A-2) and confirm with Clay.
- **Greenfield, self-contained, migration-aware:**
  - Nothing connects to Clay's systems.
  - Pick shapes that map cleanly back to Clay's account, which already uses S3, SageMaker, Bedrock and Snowflake.
- **AWS accounts:**
  - Each participant gets their own sandbox; your team lead provides the credentials (organizer email).
  - The solution targets a **single account**, so the IaC must deploy into any one of them.
  - Pick one account as the shared demo account (part of A-1).
- **Languages:**
  - **Python 3.12+** for the agent, tracing and scoring.
  - **TypeScript / Node 20+** for the thin UI, or for MCP servers if we write any in TS.
  - SQL and Bash are allowed.
- **Required / expected libraries** (Technical doc):
  - An agent framework, kept swappable.
  - The MCP SDK plus geospatial MCP servers.
  - A tracing tool, with "an OpenTelemetry-based LLM trace approach" as the suggested default.
  - The geospatial stack: rasterio, GDAL, xarray and pystac.
  - boto3.
- **Preferred libraries** (Technical doc):
  - Clay/LGND embeddings.
  - scikit-learn and numpy for scoring metrics.
  - FastAPI for the thin API.
- **Pinned versions:** the docs run RODA with `@latest`. We pin an exact version so runs are reproducible.

## LGND embeddings

We followed the link in the docs ([source.coop/clay/lgnd-embeddings](https://source.coop/clay/lgnd-embeddings)) and read the READMEs on 2026-11-02:

- **Who makes them:** [lgnd.ai](https://lgnd.ai/) builds and maintains them; the Clay account owns the listing. Licensed CC BY 4.0.
- **What they are:** Clay v1.5 embeddings of **Sentinel-2 L2A** imagery (10 m resolution), **not NAIP**. Each vector covers one [Major TOM](https://huggingface.co/Major-TOM) grid cell. Coverage is global, January 2017 to April 2026.
- **Two sizes:** 1280 m chips with 256 dimensions, or 2560 m chips with 1024 dimensions.
- **Only the monthly set exists:** `monthly-aggregated/` keeps one embedding per cell per month. The per-scene set is "coming soon".
- **Where:** `s3://us-west-2.opendata.source.coop/clay/lgnd-embeddings/`, readable with `--no-sign-request`, in **us-west-2** (our region).
- **Format:** GeoParquet 1.1, split into folders as `model_version=v1.5/collection=sentinel-2-l2a/chip_size=1280m/dims=256/geohash=9y/year=2025/month=01/*.parquet`. Columns: `chips_id`, `cell_id`, `rasters_id`, `stac_item_id`, `collection`, `datetime`, `embedding`, `bbox`, `geometry`. Each folder holds files of about 20 MB. The READMEs show DuckDB queries that filter on `bbox`.
- **Source scene index:** `rasters/collection=sentinel-2-l2a/stac.parquet` (1.3 GB, STAC GeoParquet) lists each scene's footprint, time, and per-band image URLs. It joins to the embeddings on `stac_item_id`. A `collection=naip` index is listed as "(future)".
- **Gaps:** the data ends April 2026, and some months are missing (no June 2024 or June 2025 over California or Nepal; no 2021 over Nepal; checked 2026-11-02).
- **What this means for us:**
  - The caption task can't use these embeddings for NAIP retrieval, but the NAIP embeddings below can.
  - The ADB task doesn't need them.
  - Real paths that an ADB agent may cite (for example a "Clay v1.5 Sentinel-2" bucket that RODA surfaces) can now be checked against this layout.

## Other Clay embeddings

Clay's Source Cooperative account (`s3://us-west-2.opendata.source.coop/clay/`, anonymous, us-west-2, CC BY 4.0) holds more than the LGND set. Listed 2026-11-02 from each README:

| Prefix | What | Notes |
|---|---|---|
| `clay-v1-5-naip-2/` | Clay v1.5 embeddings of **all NAIP on AWS** (2010–2021), 1024 dims, one per 256×256 px tile | Made by Development Seed for Clay. Folders mirror NAIP's (`<state>/<year>/<res>/rgbir_cog/...parquet`); columns `geometry`, `embeddings` |
| `clay-v1-5-naip/` | The first NAIP run | **Don't use:** its README says inference skipped normalization; use `-naip-2` |
| `clay-v1-5-sentinel2/` | Clay v1.5 embeddings of Sentinel-2 (2.9 TiB), 1024 dims | Partial areas (USA, Brazil, Suriname, Andhra Pradesh) as of Dec 2024; folders mirror `sentinel-s2-l2a-cogs` |
| `lgnd-clay-v1-5-sentinel-2-l2a/` | Earlier LGND set, 2560 m cells, 1024 dims | Near-global for **June 2024 and June 2025 only**, the months missing from `lgnd-embeddings` |
| `california-naip-clay-v1/` | Clay v1 embeddings of California NAIP (43.6 GiB) | Older model; no README |
| `clay-model-v0-embeddings/` | Clay v0 embeddings (`.gpq` files) | Superseded model |

## Top risks (from the Vision doc)

| Risk | Our mitigation |
|------|----------------|
| No clear reference to score against | The source says to secure it "before the event": email the organizers now (K-1). Until it arrives, draft a stand-in (E-2) |
| The live agent is flaky | Use fixture and golden runs, plus replay (K-4, G-1, D-5) |
| Gaps in MCP server coverage or maturity | Start with RODA; add from Clay's shortlist only once RODA works |
| NAIP Requester-Pays cost | Use a tiny pre-staged sample (A-2) |
| Scope creep across tasks | One task fully working first; see the "Should I work on this?" chart |

## Confirm with Clay at kickoff

*"None of these block starting the ADB agentic eval on day 1 — confirm them early so the other tracks aren't blocked."* (Vision doc)

- [ ] The **ADB scoring rubric** and one representative eval to target.
- [ ] The **ADB input data** and **harness(es) under test**: does Clay provide them, or do we build our own? *(Our addition. The Vision doc lists these as coming "from Clay".)*
- [ ] Clay's **free/priority MCP server shortlist**.
- [ ] **Example captions** + **retrieval eval set** (caption task).
- [ ] **NAIP Requester-Pays** handling (pre-staged sample vs. live pull).
- [ ] The **agent-development partner** and any agent build we should stand on. *LGND is [lgnd.ai](https://lgnd.ai/), which maintains the Clay embeddings ([LGND embeddings](#lgnd-embeddings)); still confirm whether they are also the agent partner.*
- [ ] *(Our addition)* **Which embeddings** the caption retrieval eval uses: NAIP embeddings exist (`clay-v1-5-naip-2`, see [Other Clay embeddings](#other-clay-embeddings)); confirm that's the set, or whether they'd rather we use Sentinel-2.
- [ ] If we take the **open third track**: the specific task and target.
- [ ] *(Our addition)* Which **shared GitHub org/repo** we push to. The repo is "created at kickoff"; everyone needs a GitHub account ready.

Raised during the build (our additions):

- [ ] Must the repo be **public**, or is access for Clay enough? The Vision doc's Definition of Done asks for "an open-source repo the Beneficiary can access after the event"; ours is Apache-2.0 but private.
- [ ] Does the **hackathon account's policy** allow what the cloud deployment uses: Lambda function URLs, Step Functions, DynamoDB, S3 and Bedrock? See [deploy.md](deploy.md).
- [ ] Should Clay's rubric **replace our expected tool steps** (`tasks/adb/expected.jsonl`), or should they stay as an extra workflow signal?
- [ ] Which **Bedrock models** can the hackathon account use? Our defaults (Kimi K3 as the agent, Opus 4.6 as the judge) were verified by calls in our own sandbox only ([bedrock-models.md](eval-sandbox/bedrock-models.md)).

Questions before the event can go to breaking-barriers-us-2026@amazon.com.
