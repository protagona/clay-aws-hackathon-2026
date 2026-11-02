# Backlog

Built so that about 8 people with unknown skill sets can work in parallel. Once the **trace contract** and **fixture runs** exist (in the first ~90 minutes of kickoff), every stream except the live harness builds against saved files instead of waiting on the others.

**One deliberate departure from the Technical doc.** It says to get a single trace captured end to end *before adding scoring*. With 8 people, waiting idles most of the team. So:

- The scorers and summarizer are built against **fixture** runs in parallel.
- They are not run against **live** traces until the first live trace exists (milestone CP-2).
- We still prove the loop on one task before touching a second. See the dependency picture in [visuals.md §4](visuals.md#4-who-can-work-on-what-and-when).

**Legend:**

- **Pri**: **M**ust, **S**hould, or **C**ould. These map to [requirements.md](requirements.md).
- **Skill**:
  - `py`: Python
  - `aws`: AWS / IaC
  - `web`: TypeScript UI
  - `dom`: domain or writing, so no coding is needed
  - `any`: anyone can take it
  - `all`: the whole team together
  - `a+b`: needs both skills

## Suggested streams and staffing (8 people)

| Stream | People | Skill | Owns |
|--------|--------|-------|------|
| K: Kickoff | everyone | any | K-* |
| A: Platform | 1 | aws | A-*, C-3 |
| B: Harness | 2 | py | B-*, C-2, G-1 |
| C: Tracing | 1 | py | C-1, C-4 |
| D: Evaluation | 2 | py | D-*, including D-8, the end-to-end command |
| E: Task content | 1 | dom | E-*, then G-2, G-3 |
| F: UI | 1 | web | F-* |

Short on Python people? Merge C into B, and put the strongest generalist on D. Got a non-coder? E-1 to E-3, G-2 and G-3 need no coding.

## K: Kickoff (everyone, first ~90 min)

| ID | Item | Pri | Skill | Depends | Done when |
|----|------|-----|-------|---------|-----------|
| K-1 | Confirm the unknowns with Clay: rubric, ADB input data and harness(es), MCP shortlist, captions and retrieval set, NAIP, NAIP embeddings, partner, repo, and the open-track pick if any. The full list is in requirements.md. Ask by email **before the event** where possible. **This blocks nothing:** the source says *"None of these block starting the ADB agentic eval on day 1"* | M | any | — | All questions asked; answers recorded in [requirements.md](requirements.md#confirm-with-clay-at-kickoff) as they arrive |
| K-2 | Repo skeleton: layout, Apache-2.0 LICENSE, Python project, lint and test in CI. Use the shared repo if it exists; otherwise start anywhere and push there later | M | py | K-5 | CI is green on an empty test. `main` is protected once the repo is in the shared org |
| K-3 | Ratify the [trace contract](trace-contract.md). This runs in parallel with K-2 and doesn't wait on CI | M | all | — | Agreed by the team and merged once the repo exists; changes from here on are additive only |
| K-4 | Hand-write 3 fixture runs: clean, recovered, failed | M | any | K-3 | `fixtures/runs/*` match the contract, checked by eye now and by C-1's validator later |
| K-5 | Pick the agent framework and IaC tool; record the choice in the README | M | all | — | Decision written down; see the defaults below |

## A: Platform

| ID | Item | Pri | Skill | Depends | Done when |
|----|------|-----|-------|---------|-----------|
| A-1 | IaC in **us-west-2**: runs bucket, IAM role for the harness, Bedrock model access verified. Parameterize it so anyone can deploy into their own sandbox | M | aws | K-2 | `deploy` creates it in any teammate's account; a Bedrock "hello" call succeeds with the role |
| A-2 | Pre-stage a tiny NAIP sample and a Vantor sample in the bucket, as a cost-safe fallback. The docs prefer reading at the source via MCP and suggest pre-staging when it's "faster/cheaper (Requester-Pays)". Adjust once Clay answers K-1 | M | aws | A-1 | Sample URIs are listed in the README and readable by the role |
| A-3 | Cloud runtime for the harness (a container task or Lambda). Running locally is fine on Day 1 | S | aws | A-1, B-3 | A run can be triggered in the cloud and writes to S3 |
| A-4 | Deploy from a clean checkout following the README only. Finish before the freeze (CP-5) | M | aws | A-1, A-2, and A-3 if it was done | A teammate who didn't build it deploys successfully |

## B: Harness

| ID | Item | Pri | Skill | Depends | Done when |
|----|------|-----|-------|---------|-----------|
| B-1 | Model adapter interface plus a Bedrock implementation. **Merge the interface first**, because D-2 and D-3 use it too | M | py | K-2, K-5 | The model is chosen by config only; the interface is merged by about hour 2 |
| B-2 | MCP client connected to the RODA MCP server (`uvx awslabs.roda-mcp-server`, pinned version) | M | py | K-2, K-5 | The agent can list and call RODA tools |
| B-3 | Agent loop for the ADB task: prompt in, tool calls, output out, with a step limit and timeout. Use a placeholder prompt until E-1 lands. If Clay supplies a harness (K-1), wrap theirs instead | M | py | B-1, B-2 | One synthetic prompt finishes with an `output.json` |
| B-4 | Second model config for the swap demo | M | py | B-1 | The same prompt runs on two models through config alone |
| B-5 | Add 1–2 servers from Clay's MCP shortlist. The Technical doc's Day-1 guidance suggests this; do it right after RODA works | S | py | B-2, K-1 | The agent uses at least one non-RODA tool |
| B-6 | Multi-agent mode: planner, executor, checker, emitting `agent_step` | C | py | B-3, C-2 | The trace shows nested agent steps |

## C: Tracing

| ID | Item | Pri | Skill | Depends | Done when |
|----|------|-----|-------|---------|-----------|
| C-1 | Tracer interface plus a JSONL implementation that writes `runs/<id>/` per the contract | M | py | K-2, K-3 | Unit test: fake steps produce a valid run directory |
| C-2 | Wire the tracer into the harness for every LLM call, tool call and error, with tokens and cost | M | py | B-3, C-1 | A live run produces a valid `trace.jsonl` (**CP-2**) |
| C-3 | Mirror `runs/` to S3 | M | aws | A-1, C-1 | The run appears under `s3://…/runs/<id>/` |
| C-4 | Second tracer implementation (OpenTelemetry exporter or Langfuse) for the swap demo | M | py | C-1 | The same run is visible in the second backend through config alone |

## D: Evaluation

| ID | Item | Pri | Skill | Depends | Done when |
|----|------|-----|-------|---------|-----------|
| D-1 | Workflow scorer: steps, tool errors, retries, recovery, invalid calls, duration, cost. Add the sequence check if E-3 exists (it's optional) | M | py | K-2, K-4 | All 3 fixtures are scored, and clean > recovered > failed |
| D-2 | Summarizer: a timeline of what happened plus a short LLM narrative of where it struggled | M | py | K-4, B-1 | For each fixture, a teammate who didn't write it can say from the summary alone which steps failed, whether the run recovered, and how it ended |
| D-3 | Output scorer: an LLM judge scores each rubric criterion with a rationale | M | py | K-4, E-2, B-1 | `scores.json` has per-criterion scores and rationales |
| D-4 | Report builder: `report.md` with summary, both scores, and provenance (model, tracer, rubric, inputs) | M | py | D-1, D-2, D-3 | A report is generated for each fixture (**CP-3**) |
| D-5 | `evaluate <run_dir>` CLI, which is replay mode. It doesn't run the agent, but it still calls Bedrock for the summary narrative and the judge | M | py | D-4 | Works on fixtures and on a recorded live run |
| D-6 | Judge sanity check: score known-good and known-bad outputs, and check the judge is consistent across 3 repeats | S | py+dom | D-3, E-2 | Results are written in the README; known issues noted |
| D-7 | Sensitivity script: run N variants (prompt, model or tools) and compare scores | C | py | D-5, B-4 | A table of score spread across the variants |
| D-8 | One command, `run --evaluate`: live run, then trace, then report, saved to S3 | M | py | C-2, C-3, D-5 | It works from a clean shell (**CP-4**) |

## E: Task content

| ID | Item | Pri | Skill | Depends | Done when |
|----|------|-----|-------|---------|-----------|
| E-1 | 5–10 synthetic ADB-style prompts, ranging from easy to hard | M | dom | — | `tasks/adb/prompts.jsonl` committed |
| E-2 | Rubric as data: criteria, scale and what a 1 vs. a 4 looks like. **Draft a stand-in now** and swap in Clay's when it arrives | M | dom | — | `tasks/adb/rubric.json` committed |
| E-3 | Expected workflow per prompt: which tools *should* be called, roughly in what order | S | dom | E-1 | Used by the D-1 sequence check |
| E-4 | Caption task pack: imagery sample, Clay's example captions, the retrieval eval set, and LGND embeddings. Needs its own output scorer (retrieval metrics). The public LGND embeddings cover Sentinel-2, not NAIP ([details](requirements.md#lgnd-embeddings)), so the imagery must match what Clay confirms in K-1 | C | dom+py | K-1, A-2 | `tasks/captions/` committed; a caption run is scored |

## F: UI

| ID | Item | Pri | Skill | Depends | Done when |
|----|------|-----|-------|---------|-----------|
| F-1 | Runs list and report view, reading `runs/` from local disk or S3 | S | web | K-4 | Shows fixture reports |
| F-2 | "Start run" form (task, prompt, model, tracer) backed by a thin FastAPI | S | web+py | F-1, B-3 | Launches a live run from the browser |
| F-3 | Compare runs side by side | C | web | F-1, D-7 | Two runs are shown with their score differences |

## G: Ship

| ID | Item | Pri | Skill | Depends | Done when |
|----|------|-----|-------|---------|-----------|
| G-1 | Record 2–3 golden live runs for the replay demo | M | py | D-8 | Committed under `fixtures/runs/golden/` |
| G-2 | README covering what, why, how to run, how to deploy, how to swap the model and tracer, and the replay demo | M | dom | D-5 | A newcomer runs replay in under 10 min |
| G-4 | Make the repo public in the shared org with the LICENSE, and confirm Clay can access it after the event (Vision doc's Definition of Done) | M | any | K-2 | Viewed and cloned from a logged-out browser |
| G-3 | Demo script and two rehearsals: live run, model and tracer swap, replay fallback. This one is our addition, not from the docs | M | any | D-8, G-1 | Fits the time slot and has a fallback ready |

## Default choices (change them at K-5 if the team prefers)

- **Agent framework:** Strands Agents. The docs don't prescribe one. Strands is AWS-native and supports Bedrock, MCP and OpenTelemetry. It still sits behind our own model and tracer adapters.
- **IaC:** AWS CDK in Python, so the whole team uses one language.
- **Tracing (our call):** the docs suggest an OpenTelemetry-based approach as the default. We use our own JSONL files as the always-works baseline and an OTel-based backend as the second tracer that proves swappability. Langfuse and Arize Phoenix are both OTel-compatible.
- **Judge:** a different Bedrock model from the agent's, to avoid the model grading itself.
- **Dev tooling:** the organizers suggest the **Agent Toolkit for AWS** (the AWS MCP Server plus agent skills) for coding agents. It's worth installing before Day 1.
- **Harness under test:** ours by default. If Clay provides one, we wrap it behind the same adapters and evaluate it instead.

## Turning this into GitHub issues

Each row becomes one issue. Use the ID as the title prefix and add labels for `stream:*`, `pri:*` and `skill:*`. Target days come from the timeline in [visuals.md §5](visuals.md#5-timeline). Put the "Done when" text into the issue body as its acceptance criteria.
