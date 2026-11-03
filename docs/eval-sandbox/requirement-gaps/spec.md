# Requirement gaps (NAIP paths, judge cases, caption task, geospatial libraries): Spec

**Track:** Standard   **Product stage:** Introduction (hackathon prototype; presentations 2026-11-05)
**Status:** Draft   **Last updated:** 2026-11-03

## Problem

Against `docs/requirements.md`, the evaluator has four gaps. Each one costs the people who rely on its scores.

- **Caption task:** Clay's second task is caption generation for NAIP imagery. Nobody can run or score it. Requirements: Tasks, option 2; backlog E-4.
- **NAIP paths:** a plan that cites NAIP, which sits in requester-pays buckets, always gets "unverified". So the judge can't tell real NAIP locations from made-up ones.
- **Judge sanity suite:** the suite (backlog D-6) covers the ADB task only. It predates the path check, so nothing shows that the judge acts on the path evidence.
- **Geospatial libraries:** the requirements list rasterio, GDAL, xarray and pystac as expected libraries. The repo uses none of them, and nothing records why.

## Users

- **Primary:** the hackathon team. They demo the evaluator on 2026-11-05 and need a second task and trustworthy scores.
- **Secondary:** Clay's evaluators. They inherit the repo and will swap in their own captions, retrieval set and rubric.

## Goals

- A caption run can be launched and scored end to end, like an ADB run: trace, workflow score, output score, report.
- A plan that cites NAIP gets found or not found, not unverified.
- There is recorded evidence that the judge lowers a plan's score when the path evidence says its data locations don't exist. There is also recorded evidence that the caption scorer ranks good captions above bad ones.
- Each expected geospatial library is either used where it does real work, or recorded as not needed, with the reason.

## Non-goals

- **Clay's real material:** the captions, retrieval eval set and rubric are pending (K-1). We ship stand-ins built to be replaced.
- **Corpus-level retrieval metrics:** mAP across a whole caption batch. This round scores each run on its own.
- **Image-aware judging:** the judge stays text-only. For captions the output score comes from retrieval, not from the judge.
- **Imagery beyond the sample:** Vantor imagery, and NAIP outside the pre-staged sample.
- **Live NAIP reads:** runs never read NAIP from the requester-pays buckets live. Only the one-time staging step does.
- **Text queries against Clay embeddings:** Clay v1.5 embeddings are used only to choose hard negatives.
- **Swapping the agent framework, or upgrading `mcp`:** the user declined the `mcp` upgrade, 2026-11-03.
- **xarray and GDAL as direct dependencies:** GDAL arrives bundled inside rasterio's wheels. xarray has no job here.

## User stories

- As a team member, I want to run the caption task from the UI or CLI, the same way as ADB, so that I can demo a second Clay task.
- As a team member, I want a caption scored by how well it lets someone retrieve its own tile from look-alike tiles, so that the score measures usefulness for retrieval.
- As a Clay evaluator, I want to replace the stand-in reference captions with Clay's, without code changes, so that the scores become Clay's scores.
- As a team member, I want NAIP locations in a plan checked for real, so that the judge doesn't credit or penalise them blindly.
- As a team member, I want a repeatable check that the judge penalises missing data locations, so that I can trust the path check is worth its cost.

## Acceptance criteria

### NAIP path verification

- [ ] **AC1:** `[checks] requester_pays` names `naip-analytic`. When a plan cites an existing NAIP prefix there, such as `s3://naip-analytic/ca/2020/60cm/rgbir_cog/32114/`, the path check reports it `found`. The reason is `found (requester pays)`.
- [ ] **AC2:** Given the same allowlist, when a plan cites a key that doesn't exist in `naip-analytic`, it is `not_found`.
- [ ] **AC3:** When a requester-pays bucket is not on the allowlist, it stays `unverified` with the reason `access denied (Requester Pays)`, and no signed request is sent. A unit test asserts that no signed call is made.
- [ ] **AC4:** When there are no AWS credentials, or the signed list fails or times out, the location is `unverified` with a reason, and the evaluation still completes. The 25-location limit and the 30 s budget still apply.
- [ ] **AC5:** The offline configs send no signed requests (the path check stays off there).
- [ ] **AC6:** In the deployed `embeddings` deployment, a live run whose plan cites a `naip-analytic` prefix shows it `found`. The worker's IAM grants `s3:ListBucket` only on the allowlisted buckets.

### Judge sanity cases

- [ ] **AC7:** A new judge case, F, isolates the path evidence.
  - **The case:** case A's plan, with each of its four S3 ARNs replaced by a plausible location that doesn't exist. A's trace never names those buckets, so nothing but the path check can reveal the change.
  - **The test:** with the Bedrock judge, F is scored 3 times with the path check on, and 3 times with it off (`FOUNDRY_PATH_CHECK=off`, the control).
  - **The criteria:** with the check on, F's highest data-fit score and highest output score are lower than the lowest scores of both A and the control F.
  - **Where recorded:** the checks are in `fixtures/judge-cases/expected.json` and run in `scripts/judge_sanity.py`.
  - **If the control fails** (the judge marks F down without the evidence): that is recorded as a finding. Changing the judge prompt is out of scope.
- [ ] **AC8:** The existing 13 judge sanity checks still pass.

### Caption task

- [ ] **AC9:** `tasks/captions/` holds 12 prompts. Each names one tile of the staged sample by id. The task also has `system.txt`, `expected.jsonl`, and stand-in reference captions with a "stand-in" note. The caption task appears wherever tasks are listed (CLI and UI) and can be chosen for a run.
- [ ] **AC10:** A new MCP tool, `naip_tile(tile_id)`, returns:
  - the tile as a PNG (RGB, 256 × 256);
  - its footprint (lon/lat bbox), acquisition date and source image.

  An unknown id is a tool error. The tool reads the staged sample with rasterio from a local directory. Its source:
  - **Where the sample lives:** each deployment's own runs bucket, under `samples/naip-captions/` (the user's choice, 2026-11-03).
  - **How it gets local:** before the run starts its MCP servers, the harness downloads the sample into a local cache.
  - **Never:** the requester-pays buckets.
  - **When the sample isn't staged:** the run records a note naming the staging command, and `naip_tile` returns a tool error.
- [ ] **AC10b:** A one-time staging script does two jobs:
  1. It reads the crop from NAIP (requester-pays) and writes `sample.tif` and `tiles.json` locally.
  2. It uploads them to a given deployment's runs bucket under `samples/naip-captions/`. Re-uploading needs no new NAIP reads.
  - **Re-staging:** creates a new `sample_id`. Runs and evaluations refresh their cached copy when the bucket objects change. The scorer refuses a sample whose `sample_id` doesn't match `scorer.json`.
  - **Laptop CLI runs:** use the local copy in `samples/naip-captions/` (git-ignored).
- [ ] **AC11:** A live caption run with Claude Sonnet 4.6 as the agent model on Bedrock succeeds.
  - **The trace:** the `naip_tile` step carries `images: 1`, a new optional trace field.
  - **The output:** a caption.
  - **Tool results over the size cap:** the image is kept. Only the text is cut.
- [ ] **AC12:** A caption run's output score comes from the retrieval scorer, computed as follows:
  1. The candidates are the target tile plus its 7 nearest tiles in the staged sample, by cosine similarity of their Clay v1.5 NAIP embeddings.
  2. Each candidate's reference caption is compared with the run's caption, by cosine similarity of their text embeddings.
  3. The score is 1 / (the target's rank).

  `scores.json` records the scorer name and version, the rank, the candidate tile ids, the embedding model and the reference-captions hash.
  - **For auditing:** the evaluation copies its exact inputs into the run directory as `retrieval.json`: the target, the candidates' reference captions, their Clay similarities and the caption. That keeps a record of exactly what produced the score after Clay swaps references.
  - **The judge** is not called for captions. `output.judge`, `evaluation.judge` and `templates.judge` are null, and the report says "Judge: not used (retrieval scorer)".
  - **Time limit:** the embedder's calls have a 60 s total budget. Past it, the output is unscored with an error.
- [ ] **AC13:** A deterministic scorer sanity check over all 12 tiles, with the Bedrock text embedder, gives these results:
  - **The target's own reference caption:** scores 1.0 on all 12.
  - **The generic caption "An aerial image of land.":** scores ≤ 0.5 on at least 10 of 12.
  - **A random other tile's reference caption:** scores lower than the target's own on at least 10 of 12.

  The check runs as a script and its result is recorded. If it fails, the window may be re-staged once. If it still fails, the result is recorded as a finding.
- [ ] **AC14:** The text embedder is chosen in config. The Bedrock configs use a Bedrock text-embedding model. The offline configs use a deterministic local embedder that needs no network, and tests use it.
- [ ] **AC15:** The workflow score for captions uses the expected stages `[["naip_tile"]]`.
- [ ] **AC16:** A caption run's report shows a "Retrieval" section with the target's rank, the candidates, and each candidate's similarity.
- [ ] **AC17:** ADB evaluation is unchanged. The golden-run tests and existing evaluate tests pass, and ADB's `scores.json` gains no required key.

### Geospatial libraries

- [ ] **AC18:** The STAC MCP server queries Earth Search through pystac-client. Its tool names, arguments and outputs are unchanged, and the existing STAC tests pass, including the network test against Earth Search.
- [ ] **AC19:** rasterio is a pinned runtime dependency, used by `naip_tile` and the staging script. `docs/requirements.md` records which expected library is used where, and why xarray and a separate GDAL are not used.
- [ ] **AC20:** The Lambda image builds. A live caption run's worker peaks below 1,800 MB, out of 2,048 MB.

## Success signals

- At the demo, a caption run and an ADB run both show complete reports from the same deployment.
- In live runs, cited NAIP locations come back `found` or `not_found`, never `unverified`.
- `scripts/judge_sanity.py` and the caption-scorer check both pass on the day of the demo.

## Constraints

- **Region and account:** us-west-2, sandbox account 283171504459, Foundry stacks only (standing authorisation).
- **Requester-pays costs:**
  - Staging is one-time: about 10 MB of windowed reads from one NAIP image, plus a handful of list requests.
  - The path check sends at most 25 signed list requests per evaluation, at $0.000005 each.
- **Dependency versions:** pinned exactly, each released at least two weeks ago.
  - rasterio 1.5.2 (released 2026-09-30); manylinux aarch64 cp312 wheel of 37.7 MB with GDAL bundled.
  - pystac-client 0.9.1 (released 2026-10-20).
  - Source: PyPI JSON, read 2026-11-03.
- **Vision model:** Claude Sonnet 4.6 (`us.anthropic.claude-sonnet-4-6`) and Haiku 4.5 read an image returned by an MCP tool through Strands and Converse. In a spike on 2026-11-03, both answered "Red" for a red tile.
  - Kimi K3, Kimi K2.5 and Qwen3-VL reject an image in a tool result ("This model doesn't support the image field for user messages"), although `list-foundation-models` lists image input for them.
  - Nova 2 Lite accepts the image but ignores it.
- **Strands passes MCP images through:** strands-agents 1.62.0 turns MCP image content into Converse image blocks (`strands/tools/mcp/mcp_client.py:1670`).
- **Embeddings layout:** `clay-v1-5-naip-2` keeps one Parquet file per NAIP image (1,968 rows for a 60 cm CA 2020 image, 1,024 dimensions), row-major from the north-west in 256-pixel tiles. California coverage ends in 2020. Checked 2026-11-03.
- **Deadline:** build and verify on 2026-11-03 and 2026-11-04.

## Assumptions and open questions

- **Assumption: the reference captions are model-written.** The stand-in reference captions are written by Claude from the tile images, then reviewed by the team.
  - **If wrong:** the scores measure agreement with Claude, not with Clay. Mitigation: the caption file is data, swapped without code changes (story 3).
  - **Owner:** user. **Default:** ship the stand-ins, labelled as such.
- **Assumption: 64 tiles are enough.** The sample is one 8 × 8-tile window (2,048 × 2,048 px) from `m_3211513_se_11_060_20200524` (El Centro, CA, 2020, 60 cm), chosen for varied land cover: highway and rail, river, houses, irrigated and plowed fields.
  - **If wrong:** the hard negatives are too easy or too alike. AC13 shows which.
- **Resolved: Titan Text Embeddings V2.** `amazon.titan-embed-text-v2:0` accepts `dimensions` 1024, 512 or 256 and rejects 384. Tested live on 2026-11-03; 8 input tokens for a short caption.
- **Resolved: S3 list price.** $0.000005 per request in us-west-2 (AWS Price List API, `USW2-Requests-Tier1`, 2026-11-03), so at most $0.000125 per evaluation.
- **Open: which embeddings Clay's retrieval eval uses** (K-1).
  - **Owner:** user, with Clay.
  - **Default:** `clay-v1-5-naip-2`.
