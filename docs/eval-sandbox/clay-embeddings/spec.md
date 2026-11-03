# Clay embeddings and path checks: Spec

**Track:** Standard   **Product stage:** Introduction
**Status:** Draft   **Last updated:** 2026-11-02

## Problem

Clay's engineers can't tell from a run's score whether the access details in an agent's dataset-build plan are real. The judge sees only a 200-character preview of each tool result, so it can only guess whether a cited bucket exists. In the golden run `golden-gpt-oss-120b-adb-001` it wrote that the Clay bucket paths "appear fabricated" and could not say for sure. Separately, the agent under test can use only general AWS discovery tools (RODA) and scene search (STAC), so runs never show whether an agent can use Clay's own product, its embeddings, to build a dataset. That is the workflow Clay most wants to evaluate.

## Users

- **Primary:** Clay's engineers and the hackathon team, who run and compare evaluations in the Foundry UI or CLI and read `report.md` / `scores.json`. They know geospatial data and adopt early.
- **Secondary:** hackathon judges watching the demo, who need to see a score backed by evidence.

## Goals

- A run's report shows, for every S3 location the plan cites, whether it exists, and the judge sees the same evidence when it scores `feasibility`.
- The agent can search Clay v1.5 embeddings (LGND, Sentinel-2) for an area: what months exist, places that look like a given place, and where a place changed between two months.
- At least two ADB prompts reward using the embeddings, and the workflow scorer can tell when an agent skipped them.

## Non-goals

- **The caption task, and Clay's other embedding sets.** NAIP embeddings exist (`clay-v1-5-naip-2`, see `docs/requirements.md` "Other Clay embeddings"), but the caption task waits for Clay's answer (K-1). The server here uses only the LGND monthly set.
- **Changing how `overall` is computed or editing the rubric.** The path check feeds the judge and the report; the formula, the weights in `[scoring]` and `adb-rubric-v0` stay as they are. The judge's `feasibility` score can still move, because it now sees evidence; that is the point.
- **Proving a path appeared in a tool result.** Traces keep only the first 1,000 characters of each result (`hooks.py` `PREVIEW_CHARS`); storing full results is a separate change.
- **Checking HTTP(S) URLs that aren't S3** (STAC APIs, websites). The check never fetches arbitrary URLs.
- **Signed requests or requester-pays reads.** The check runs without credentials and never incurs data charges.
- **The 2560 m / 1024-dimension embeddings.** Only the 1280 m / 256-dimension set.
- **Re-recording the golden runs.**

## User stories

- As a Clay engineer, I want each cited S3 location marked as found, not found, or unverifiable, so that I can trust or dismiss a plan's access details without checking by hand.
- As a Clay engineer, I want the agent to be able to query Clay embeddings, so that evaluations show how well an agent uses Clay's product.
- As the team, I want prompts whose expected workflow includes the embeddings, so that a run that ignores them scores lower on workflow.

## Acceptance criteria

**Path check**

- [ ] AC1: Given a plan that cites S3 locations as `s3://bucket/key`, `arn:aws:s3:::bucket/key`, `https://bucket.s3[.region].amazonaws.com/key` or `https://s3[.region].amazonaws.com/bucket/key`, when the run is evaluated, then `scores.json` has a `paths` section listing each distinct location once, with a status of exactly one of `found`, `not_found` or `unverified`, plus the reason for `unverified`.
- [ ] AC2: `found` means an anonymous list of the bucket returned at least one object whose key starts with the cited key, or, when only a bucket is cited, that the list succeeded (even if the bucket is empty). `not_found` means the bucket doesn't exist, or it listed without error and returned no matching object. `unverified` covers access denied (including requester-pays buckets), timeouts and any other error.
- [ ] AC3: The check makes no request other than anonymous S3 list requests, sends no credentials, and never reads object contents.
- [ ] AC4: At most 25 distinct locations are checked per run (the rest are listed as `unverified` with reason `not checked (limit of 25)`); each location gets a single attempt with 3 s connect and 3 s read timeouts; the whole check returns within 30 s even if requests hang (unfinished ones become `unverified` with reason `not checked (time budget)`).
- [ ] AC5: The judge's input includes an evaluator-checks block, placed outside the untrusted run data, that lists each location with its status and reason, and tells the judge, for the feasibility criterion, that "bucket does not exist" means wrong access details, "no object with this prefix" means a wrong path or an unmarked template, and `unverified` is no evidence either way. Text in the agent's output or tool results that imitates this block is neutralised, so it cannot pass as evaluator evidence.
- [ ] AC6: `report.md` shows a "Cited S3 locations" table with each location, status and reason; text from the plan is escaped like other report text.
- [ ] AC7: Running the path check with real S3 on the output of `golden-gpt-oss-120b-adb-001` marks `s3://us-west-2.opendata.source.coop/clay/clay-v1-5-sentinel2/sentinel-s2-l2a-cogs/` as `found` (the judge's stored rationale called these paths "fabricated"; they exist, checked 2026-11-02) and `s3://usgs-landsat/collection02/` as `unverified` with reason `access denied (Requester Pays)`; a made-up bucket in a test string is `not_found` with reason `bucket does not exist`.
- [ ] AC8: Setting `[checks] paths = false` in the config, or `FOUNDRY_PATH_CHECK=off` in the environment, skips the check; `scores.json` then has `paths: null` and the report says the check was off. The `offline*.toml` configs set it off (offline demo replay must work without network). The test suite makes no path-check network calls except the one network-marked AC7 test.

**Embeddings server**

- [ ] AC9: A new in-repo MCP server `embeddings` is registered in every config that registers `stac`, runs without credentials, and exposes exactly three tools: `embedding_coverage`, `similar_places`, `embedding_change`.
- [ ] AC10: `embedding_coverage(bbox)` (box at most 10° by 10°) returns the year-months that have embeddings for the partitions covering the box, and names the missing months between the first and last available one (for example, no June 2024 over California).
- [ ] AC11: `similar_places(lon, lat, month, search_bbox, k)`, with `month` as `"YYYY-MM"` and the point inside `search_bbox`, returns up to `k` (default 10, maximum 25) cells whose centres are inside `search_bbox`, ranked by cosine similarity to the cell containing the point (excluding that cell), each with its bounding box, similarity, Sentinel-2 scene id (`stac_item_id`) and date.
- [ ] AC12: `embedding_change(bbox, from_month, to_month)` returns the number of cells present in both months, their mean cosine similarity, and the 10 cells that changed most (lowest similarity) with bounding box, similarity and both scene ids.
- [ ] AC13: Invalid input (a box over the size limit, a month with no data, a point with no cell, a point outside `search_bbox`, coordinates out of range, `k` over 25) returns a tool error that says what was wrong and what to try; the server doesn't crash. The limits are: `search_bbox` and the change `bbox` at most 1° by 1°, and at most 2 geohash partitions read per month.
- [ ] AC14: Every tool result fits within the agent's tool-result limit (8,000 characters by default).
- [ ] AC15: In the deployed worker (Lambda, us-west-2), each embeddings tool call returns within 60 s and the worker stays within its 2,048 MB memory with headroom (max memory used under 1,800 MB), shown by a live run's trace durations and the Lambda's reported max memory. *(Relaxed from 20 s on 2026-11-03 by the user's pre-agreed rule: `embedding_change` measured 27.4 s live.)*
- [ ] AC16: The Lambda image includes everything the server needs at run time; it downloads no DuckDB extensions after start.

**Prompts and workflow**

- [ ] AC17: `tasks/adb/prompts.jsonl` gains two prompts, one answered best with `embedding_change` and one with `similar_places`, and `expected.jsonl` gains matching entries: the embeddings tool, then `search_items` or `get_item`.
- [ ] AC18: The system prompt names the embeddings tools and when to use them, and still forbids inventing buckets.
- [ ] AC19: The full test suite and `make lint` pass, including offline runs for every prompt id. Tests that download embeddings run only when `FOUNDRY_LIVE_EMBEDDINGS=1` is set, so CI downloads none.
- [ ] AC20: One live Bedrock run on each new prompt, in a freshly deployed named cloud deployment (so the new task files are seeded), completes with at least one embeddings tool call and is evaluated with path checks on.

## Success signals

- In a batch over all ADB prompts, the share of runs with at least one `not_found` location is reported, and judges' `feasibility` scores for those runs are lower than for runs with none.
- Live runs on the two new prompts call the embeddings tools and cite scene ids that `get_item` can fetch.
- During the demo, a plan's fabricated bucket is visibly flagged in the report.

## Constraints

- **Data:** `s3://us-west-2.opendata.source.coop/clay/lgnd-embeddings/monthly-aggregated/`, anonymous, us-west-2, CC BY 4.0; one geohash-month of 1280 m embeddings is up to ~330 MB in two row groups (checked 2026-11-02 with `parquet_metadata`), so a query reads most of a partition. Data ends April 2026 and has gaps (no June 2024 or June 2025 seen over California or Nepal; no 2021 over Nepal).
- **Runtime:** the worker Lambda has 2,048 MB memory, 2,048 MiB of `/tmp` and a 900 s timeout (`infra/foundry_cloud/stacks.py`); a run is capped at 300 s (`[limits] max_seconds`).
- **MCP subprocess environment:** only the allowlisted variables plus the server's `env` reach it (`engine/harness/mcp.py`), so no AWS credentials.
- **Dependencies:** pinned in `pyproject.toml` and `uv.lock`; a new dependency's version must be at least two weeks old.
- **Deadline:** usable for the hackathon, which starts 2026-11-03.

## Assumptions and open questions

- Assumption: a partition's `geohash=` value is the 2-character geohash of each cell's centre. If wrong, area queries miss cells; checked in Build against real data.
- Assumption: reading a partition from Lambda in us-west-2 takes a few seconds (it took ~28 s from a laptop in Europe). If wrong, AC15 fails and the limits in AC13 shrink. Proven by a live run (AC15).
- Assumption: anonymous S3 list requests reach buckets in other regions (via the region redirect). If wrong, those become `unverified`, not `not_found`. Checked in Build.
- Open: does Clay want embeddings in the ADB eval, and which embeddings version? Owner: the team, at kickoff (K-1). Default: include them, v1.5 / 1280 m.
- Open: which embeddings the caption task uses (NAIP `clay-v1-5-naip-2` or Sentinel-2). Owner: Clay (K-1). Default: out of scope here.
