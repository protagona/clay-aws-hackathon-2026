# Technical Environment Document: Clay Earth Dataset Foundry


## Hackathon Approach and Expectations

Read this first. It sets expectations for how to use the rest of this document.

- **Build an evaluation sandbox, not a model.** The goal is to **observe, summarize, and score agentic geospatial workflows**. The agent runs a known task by calling geospatial tools via MCP; you capture its traces and score the run. Spend your time on the observe-summarize-score loop.
- **Three tasks, pick one or more.** Teams should make progress on one or more of the three tasks below; the **ADB agentic eval** is the recommended starting point.
- **Keep the model and the tracing tool swappable.** Use Amazon Bedrock for the agent, behind an abstraction. The team **recommends** a tracing/observability tool but must keep it swappable—nothing hard-wired.
- **Greenfield, self-contained, migration-aware.** Build in an empty sandbox that mirrors the setup without integrating into Clay's systems; choose shapes that map cleanly back to Clay's account.
- **Data: public or synthetic only.** NAIP imagery, Vantor open data, LGND embeddings, and synthetic prompts/captions. Mind NAIP's Requester-Pays billing.
- **Reliability: record traces.** Capture traces so a flaky live agent can be replaced by replaying recorded traces through the scoring sandbox for the demo.

---

## Project Technical Summary

- **Project Name**: Clay Earth Dataset Foundry (MVP — agentic-workflow evaluation sandbox)
- **Primary Runtime Environment**: Cloud
- **Cloud Provider**: AWS
- **Target Deployment Model**: Flexible (serverless / containers / K8s), single sandbox account.
- **Customer Team Experience**: Deep geospatial-ML practice—open Earth foundation model, 15.2B published embeddings (Apache-2.0), an existing evaluation harness, continental-scale inference; existing use of S3, SageMaker, Bedrock, Snowflake; maintains a catalog of ~99 MCP servers.

---

## The Three Tasks (input data & scoring)

| Task | Agent does | Input data (public/synthetic) | Score against |
|------|-----------|-------------------------------|---------------|
| **ADB agentic eval** *(start here)* | Performs an ADB-style AI/dataset-build task under test | Input dataset + harness(es) under test + **synthetic prompts** (teams bring) | **Scoring rubric**; output is the **traces** gathered from observing the system |
| **Caption generation** (Clay v2) | Imagery → training captions; enable retrieval (e.g., "find railroads") | **NAIP** high-res imagery + **sample Vantor data** (`s3://vantor-opendata`) | **Example captions** (Clay has them) + retrieval **evaluation datasets** |
| **Open track** (team's choice) | A third task from Clay's eval lists or a dataset-build target (e.g., flooding / building-value layer) | Public/synthetic, task-dependent | Confirm the specific target + reference with Clay before investing |

> **Getting started (Day 1):** Start with the **ADB agentic eval**. Stand up the LLM harness on Bedrock, wire in the **RODA MCP server** (`uvx awslabs.roda-mcp-server@latest`) plus one or two from Clay's priority MCP shortlist, and get a single trace captured end to end before adding scoring. Prove the observe → summarize → score loop on one task before touching a second.

---

## Programming Languages

### Required Languages

| Language | Version | Purpose | Rationale |
|----------|---------|---------|-----------|
| Python | 3.12+ | Agent/harness, MCP wiring, trace capture/scoring, geospatial processing | Geospatial + agent/MCP ecosystems live in Python; matches Clay |
| TypeScript / JavaScript | 5.x / Node 20+ | Thin UI; MCP servers if authored in TS | Standard thin front end; strong MCP support |

### Permitted Languages

SQL (lightweight trace/metadata store), Bash (sandbox scripting).

---

## Frameworks and Libraries

### Required / Expected

| Framework/Library | Domain | Rationale |
|-------------------|--------|-----------|
| An agent framework (kept swappable) | LLM harness: plan → tool-call → task | The agentic workflow under evaluation; framework not prescribed |
| MCP SDK + geospatial MCP server(s) | Expose geospatial tools to the agent | RODA MCP server + Clay's free/priority shortlist from 99 |
| A tracing/observability tool (team recommends, swappable) | Capture + summarize agent traces | Confirmed: team chooses, must stay swappable (an OpenTelemetry-based LLM trace approach is a reasonable default) |
| Geospatial stack (rasterio / GDAL / xarray / pystac) | Imagery handling, STAC assets | Standard toolkit for NAIP/public archives |
| AWS SDK for Python (boto3) | S3, Bedrock access | Native AWS integration |

### Preferred Libraries

| Library | Purpose | Use When |
|---------|---------|----------|
| Clay / LGND embeddings (Apache-2.0) | Imagery embeddings for captioning/retrieval | Reuse rather than train |
| scikit-learn / numpy | Scoring metrics (output vs. reference) | Scoring the task output |
| FastAPI | Thin API behind the UI | Launching runs, serving reports |

---

## Models and AI Services

- **Provider**: Amazon Bedrock for the agent's reasoning, behind an abstraction so the model is easy to swap.
- **Embeddings**: Clay / LGND embeddings for the caption/retrieval task.
- **MCP**: The agent reaches geospatial capabilities through MCP servers. Start with the **RODA MCP server** (AWS, Apache-2.0) and Clay's **free/priority shortlist** from its 99 servers.

---

## Observability and Evaluation (the heart of the build)

- **Capture**: record every agent step—tool calls, arguments, sequence, failures/retries, latency, cost.
- **Summarize**: turn raw traces into a readable summary of what the agent did and where it struggled (Clay's explicit ask: "summarize traces from agentic activity").
- **Score — two dimensions**:
  - **Workflow (from traces)**: tool-call correctness and sequencing, error recovery, steps/cost.
  - **Output**: quality vs. the task's reference—**rubric** (ADB) or **example captions + retrieval eval set** (captions).
- **Report**: a per-run evaluation report combining the summary and the scores.
- **Swappable tooling**: the tracing/observability tool is the team's recommendation but must sit behind an abstraction; same for the model.
- **Fallback**: persist traces so recorded runs can be replayed through scoring if the live agent misbehaves.

---

## Cloud Environment

### Cloud Provider
- **Primary Provider**: AWS
- **Account Structure**: Single empty hackathon sandbox account (greenfield). No connection to Clay's production account.
- **Regions**: Note **NAIP is in us-west-2 and Requester-Pays**—account for region and billing (pre-stage a small sample if helpful).

### Confirmed Data Sources

| Source | What | Access notes |
|--------|------|--------------|
| NAIP on AWS | High-res (0.3–1 m) 4-band US aerial imagery | `s3://naip-analytic` (MRF), `naip-source` (GeoTIFF), `naip-visualization` (RGB COG); **Requester-Pays**, **us-west-2** |
| Vantor open data | Sample vector data for captioning | `s3://vantor-opendata` |
| LGND embeddings | Clay/LGND embeddings | https://source.coop/clay/lgnd-embeddings |
| RODA MCP server | AWS Apache-2.0 MCP server for Registry of Open Data discovery/sampling | `uvx awslabs.roda-mcp-server@latest` |
| MCP server catalog | Clay's ~99 MCP servers | Clay to share **free + priority** shortlist |

### Service Disallow List

| Service | Reason | Alternative |
|---------|--------|-------------|
| SageMaker HyperPod / large Trainium clusters | Overkill for a sandbox eval slice | Modest instance / managed endpoint / mocked inference |
| Multi-account / cross-account deployment | Out of MVP scope | Single sandbox account |
| Full Snowflake/Iceberg/Athena warehouse | Heavy; not needed to prove the loop | Lightweight trace/metadata store |
| Any customer-private/sensitive data | Sandbox is public/synthetic only | NAIP, Vantor, LGND embeddings, synthetic prompts/captions |

---

## Preferred Technologies and Patterns

| Pattern | When to Use | When Not to Use |
|---------|-------------|-----------------|
| Observe → summarize → score the agent | Default: every run is traced, summarized, and scored | Never evaluate on final output alone with no trace |
| Model + tracing tool behind abstractions | Keep both swappable | Never hard-wire a specific model or tracing vendor |
| Recorded-trace replay | Demo reliability and offline scoring | — |
| Read data at the source via MCP | Stream NAIP/Vantor through MCP tools | When a tiny pre-staged sample is faster/cheaper (Requester-Pays) |
| Greenfield, migration-aware | Self-contained sandbox, shapes that map to Clay's account | When a shortcut makes migration harder |

---

## Stretch Goals (if the team moves fast)

- Evaluate a **second (or third) task** to show the loop generalizes.
- **Multi-agent** (planner/executor/checker) traces.
- **Dynamic MCP tool discovery** across Clay's 99-server catalog.
- A richer **trace-summary dashboard**.
- **Sensitivity analysis** — vary inputs, prompts, tool selection, or model and measure how much the output and the trace/scores change (robustness of the workflow and the eval).
