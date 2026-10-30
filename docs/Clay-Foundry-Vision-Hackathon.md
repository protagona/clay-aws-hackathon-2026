# Vision Document: Clay Earth Dataset Foundry

> **How to use this document (developer teams):** This is your context and scope for the build, not a rigid spec. Read the Executive Summary and "The Three Tasks" first, pick one task to drive end to end (start with the ADB agentic eval), and treat the "In Scope / Out of Scope" lists as your guardrails for a 2-day build. A short list of items to confirm with Clay at kickoff is at the end.

## Executive Summary

The Earth Dataset Foundry is an open platform that helps public-interest practitioners turn free satellite imagery into usable, trustworthy datasets. The hardest and least-served part of that mission—and the focus of this hackathon—is **evaluating agentic geospatial workflows**: when an AI agent plans and runs a dataset build by calling geospatial tools, how do you observe what it did and judge whether it did it well? Clay has the ingredients (known tasks, input data, example outputs, and a large catalog of MCP servers) but has never had the capacity to connect them into an evaluation system. The hackathon MVP builds that system: an **observability + evaluation sandbox** where an LLM harness calls geospatial tools over MCP, the agent's activity is captured as traces, and those traces plus the task output are summarized and scored against a rubric or reference examples. The point to prove is that an agentic geospatial workflow can be **observed, summarized, and scored end to end**, on one or more of Clay's real tasks.

## Business Context

### Problem Statement

Organizations working on urgent public problems have more satellite data than ever but lack the practical datasets they need, and building those datasets increasingly means orchestrating AI agents that call many specialized geospatial tools. The bottleneck Clay faces is not running an agent—it is **knowing whether to trust one**. Clay has long lists of evaluations it has never had capacity to run, a new text-native Clay model that needs synthetic caption data it cannot bandwidth-test, and partners (e.g., the Asian Development Bank) who need custom evals to understand how to use AI to build datasets. Without a way to observe and score agentic workflows, every agent-built dataset is a black box.

### Business Drivers

- **Evaluating agentic workflows is the #1 unmet need.** Clay has assembled tasks, input data, and tooling but lacks the observability + scoring layer to judge agentic runs.
- **Clay v2 depends on caption data nobody has tested.** The next, text-native Clay model is trained on synthetic captions of satellite imagery; Clay has no bandwidth to evaluate those caption workflows.
- **Partners need eval capacity Clay cannot supply.** ADB and others need custom evals for AI-assisted dataset building; Clay's eval backlog is real and growing.
- **The ingredients already exist.** Known tasks, public input data (NAIP, Vantor), Clay/LGND embeddings, and a catalog of 99 MCP servers mean a team can focus on the evaluation loop rather than assembling raw materials.

### Target Users and Stakeholders

| User Type | Description | Primary Need |
|-----------|-------------|--------------|
| Clay model & data team | Build and improve the Clay model and Foundry datasets | A way to evaluate agentic caption/dataset-build workflows without hand-checking every run |
| Partner organizations (e.g., ADB) | Need custom evals for AI-assisted dataset building | A reusable harness that scores an agent against their tasks and rubric |
| Agent / harness developers | Build the agentic workflows under test | Observable traces and clear scores that show where a workflow succeeds or fails |
| Domain experts | Judge whether outputs are trustworthy | Summarized traces + output scored against a rubric or reference examples |

### Business Constraints

- Open and reproducible by default (Apache-2.0, consistent with Clay's existing releases).
- Trust and provenance are first-class: every scored run keeps its traces, inputs, and the rubric/reference it was judged against.
- Public or synthetic data only for the hackathon (NAIP, Vantor open data, LGND embeddings, synthetic prompts/captions).
- Keep the model and the tracing tool behind abstractions so both are swappable.

---

## Solution Concept and Terminology

This section grounds the hackathon in how Clay frames the problem and sets shared vocabulary for the teams.

### Core Concepts

| Term | Definition |
|------|------------|
| Task | A known unit of work to evaluate an agent on. Three are available this hackathon (below); teams pick one or more. |
| LLM harness | The agent (on Amazon Bedrock, kept swappable) that performs a task by calling geospatial tools. |
| MCP tools | Geospatial capabilities the agent reaches via Model Context Protocol servers. Clay maintains ~99 and will share a free/priority shortlist. |
| Trace | The captured record of the agent's activity—tool calls, sequence, failures, cost—produced while it runs. |
| Observability sandbox | The component that captures and summarizes traces so a human (or a score) can judge the run. |
| Scoring / rubric | How a run is judged: trace-derived workflow signals plus output quality vs. a rubric or reference examples. |

### Design Principles

- **Evaluating the agent is the product.** The deliverable is the observe-summarize-score loop, not a one-off dataset.
- **Known task + known input + a reference to score against.** Every evaluated task needs all three; the scoping session confirmed these for the first two tasks.
- **Keep the model and tracing tool swappable.** The team recommends a tracing/observability tool but must not hard-wire it.
- **Recorded-trace fallback.** If a live agent is flaky, replay recorded traces through the scoring sandbox so the demo always lands.

### The Three Tasks (teams pick one or more)

Avery confirmed teams should show up able to work on one or more of three tasks, with progress on each.

1. **ADB agentic eval (recommended starting task).** Evaluate an agent (or set of agentic harnesses) on an ADB-style "how to use AI / build datasets" task. Needs: input data, the harness(es) under test, a scoring rubric, and prompts (teams bring synthetic prompts); the output is the source of traces gathered from observing the system. The eval scores the agentic workflow itself.
2. **Caption generation (for the text-native Clay v2).** Input high-resolution satellite imagery (NAIP) plus sample Vantor data; the agent develops training captions and enables retrieval (e.g., "find railroads"). Clay has example captions, and evaluation datasets check whether captions support the retrieval tasks.
3. **Open track (team's choice of a third task).** Teams that want a third target can take one from Clay's eval lists or a dataset-build use case (e.g., a flooding or building-value layer). Treat this as optional and confirm the specific pick with Clay before investing in it.

---

## MVP Scope

### MVP Objective

Build an observability + evaluation sandbox that runs an LLM harness against a known Clay task, lets the agent call geospatial tools via MCP, captures the agent's traces, and summarizes and scores both the workflow and the output against a rubric or reference examples—proving an agentic geospatial workflow can be evaluated end to end.

### MVP Success Criteria

- [ ] An LLM harness performs a chosen task (ADB eval or caption generation) by calling geospatial tools via MCP over public/synthetic input data.
- [ ] The agent's activity is captured as **traces** in an observability sandbox and **summarized** into a human-readable view.
- [ ] The run is **scored** on two dimensions: workflow signals from the traces (tool-call correctness/sequence, failures, steps/cost) and output quality vs. a rubric or reference examples.
- [ ] The sandbox produces an **evaluation report** for the run.
- [ ] The tracing/observability tool and the model are both **swappable** (behind an abstraction).
- [ ] If the live agent fails, **recorded traces** can be replayed through the scoring sandbox to still produce a report.

### Features In Scope (MVP)

| Feature | Description | Priority | Rationale for Inclusion |
|---------|-------------|----------|------------------------|
| LLM harness over MCP tools | Agent on Bedrock performs the task by calling geospatial MCP tools | Must Have | The agentic workflow is what we evaluate |
| Trace capture | Record the agent's tool calls, sequence, failures, cost | Must Have | No evaluation without observability |
| Trace summarization | Summarize traces into a readable view of what the agent did | Must Have | Clay explicitly wants to "summarize traces from agentic activity" |
| Scoring against rubric/reference | Score workflow + output vs. a rubric (ADB) or example captions | Must Have | A score is the point of the eval |
| Evaluation report | Per-run report combining summary + scores | Must Have | The deliverable a practitioner/partner reads |
| Swappable tracing tool + model | Abstraction over the observability tool and the Bedrock model | Must Have | Confirmed requirement: team recommends, keep swappable |
| Recorded-trace replay (fallback) | Replay stored traces through scoring when the live agent is flaky | Must Have | Keeps the demo reliable |
| Second task | Run the loop on a second of the three tasks | Exploratory | Shows the eval generalizes if time allows |
| Multi-agent traces | Planner/executor/checker traces | Exploratory | Bolder architecture if time allows |
| Minimal UI | Thin view to launch a run and read the report | Should Have | Makes the loop demonstrable |

### Features Explicitly Out of Scope (MVP)

| Feature | Reason for Deferral |
|---------|---------------------|
| Training the Clay v2 model itself | The hackathon evaluates workflows, it does not train the model |
| Running Clay's entire eval backlog | One (or a few) representative evals prove the loop |
| Partner-account deployment | Not needed to prove the eval loop; sandbox only |
| Continental-scale builds / Trainium-HyperPod orchestration | Exceeds a 2-day sandbox |
| Full production observability + warehouse stack (Snowflake/Iceberg/Athena, Managed Grafana) | Represent with lightweight tooling for the slice |

### MVP User Journeys

#### Journey 1: Evaluate an agentic run on a known task
1. A developer selects a task (e.g., ADB eval) and provides its input data and synthetic prompts.
2. The LLM harness runs, calling geospatial MCP tools to perform the task.
3. The observability sandbox captures the agent's traces and summarizes them.
4. The scorer rates the workflow (from traces) and the output (vs. rubric/examples).
5. The sandbox emits an evaluation report.
**Outcome**: A trustworthy, inspectable judgment of how the agent performed—not just a final artifact.
**Limitation vs Full Vision**: One task, bounded input, representative rubric; not Clay's full eval catalog.

#### (Stretch) Journey 2: Robustness via sensitivity analysis
1. Re-run the task while varying inputs, prompts, tool selection, or model.
2. The sandbox compares traces and scores across variants.
3. The report shows how stable the workflow and the evaluation are.
**Outcome**: Shows whether the agent's performance and the eval itself are robust.
**Why this is a stretch**: Requires the core loop solid first, plus multiple runs and comparison tooling.

### MVP Definition of Done

- [ ] A chosen task runs end to end: harness → MCP tools → trace capture → summarize → score → report.
- [ ] Scoring covers both workflow and output, against a rubric or example references.
- [ ] Tracing tool and model demonstrated as swappable.
- [ ] Recorded-trace fallback verified.
- [ ] Code committed to an open-source repo the Beneficiary can access after the event.

---

## Risks and Dependencies

### Key Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| No clear reference to score against for the chosen task | Medium | High | Confirmed sources: ADB rubric + synthetic prompts; example captions + retrieval eval set. Secure these before the event |
| Live agent flaky in a 2-day build | Medium | High | Recorded-trace replay through the scoring sandbox |
| MCP server coverage/maturity gaps | Medium | Medium | Use Clay's free/priority shortlist from the 99; RODA MCP server as a known-good tool |
| NAIP Requester-Pays cost in the sandbox | Medium | Medium | Pre-stage a small NAIP sample; confirm cost handling |
| Scope creep across all three tasks | Medium | Medium | One task fully working first; others are stretch |

### External Dependencies

- **Input imagery**: NAIP on AWS — https://registry.opendata.aws/naip/ (Requester-Pays, us-west-2).
- **Sample vector data**: `s3://vantor-opendata`.
- **Embeddings**: LGND embeddings (Clay) — https://source.coop/clay/lgnd-embeddings.
- **Geospatial MCP tool**: RODA MCP server (AWS, Apache-2.0) — https://aws.amazon.com/blogs/opensource/introducing-mcp-server-for-registry-of-open-data-on-aws/ — plus Clay's free/priority shortlist from 99 MCP servers.
- **ADB task**: input data, harness(es), scoring rubric (from Clay).
- **Caption task**: example captions + retrieval evaluation set (from Clay).
- Empty AWS sandbox; open-source repo created at kickoff.

### To Confirm with Clay at Kickoff

None of these block starting the ADB agentic eval on day 1 — confirm them early so the other tracks aren't blocked.

- [ ] The **free/priority MCP servers** (from Clay's ~99) to start with.
- [ ] The **ADB scoring rubric** (and one representative eval to target).
- [ ] The **example captions** and the **retrieval evaluation set** for the caption task.
- [ ] How to handle **NAIP Requester-Pays** in the sandbox (pre-staged sample vs. live pull).
- [ ] The **co-listed agent-development partner** and any provided agent build to stand on (note: "Legend" may refer to LGND, the embeddings provider — confirm).
- [ ] If a team takes the **open third track**, the specific task/target with Clay.
