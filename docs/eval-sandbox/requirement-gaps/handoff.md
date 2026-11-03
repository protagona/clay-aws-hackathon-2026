# Requirement gaps: Handoff

**What shipped:** all four gaps from `docs/requirements.md`, on `prototype` and live in the `embeddings` deployment (build `a0f8702-888cce56` plus the fix commit below):
- **Caption task:** runs and is scored end to end, by retrieval against Clay NAIP look-alikes.
- **NAIP locations:** cited NAIP locations are checked for real, with signed requester-pays lists.
- **Judge evidence:** a judge case proves the judge acts on the path evidence.
- **Geospatial libraries:** rasterio and pystac-client do real work, and xarray's absence is explained.

**Verification**
- [x] **AC1–AC4:**
  - `tests/test_paths.py` (37 passed): signed lister mapping, "no signed call for a non-allowlisted bucket", the "nothing signed is built" stub, the limit and the budget.
  - Live `-m network -k requester_pays_live` against `naip-analytic`: passed.
  - Verify-run edge case: 30 NAIP refs give 25 signed calls, 0 unsigned, and 5 "limit of 25".
- [x] **AC5:** `test_offline_configs_off`. Offline configs have `paths = false` and no `requester_pays`.
- [x] **AC6:** `tests/test_infra.py`: `s3:ListBucket` on exactly the three NAIP ARNs. Live run `2026-11-03T12-21-22Z_ac6-naip_1a21` was evaluated by the deployed worker: 4 `naip-analytic` locations, all `found (requester pays)` (`evidence/live-ac6.md`).
- [x] **AC7:** live `scripts/judge_sanity.py` (`evidence/judge-sanity.md`).
  - F scores 0.58, with data-fit 3, on all 3 repeats.
  - A, and F with the path check off, both score 0.92 with data-fit 4.
  - All of F's paths came back `not_found`. Cost $0.59.
- [x] **AC8:** the same run. The existing 13 checks pass with the path check on (A 0.92, B 0.33, C 0.08, D 0.00, E 0.17, spread 0).
- [x] **AC9:** `test_caption_task_pack`. The UI and API task list now shows `captions` (`tests/test_api.py` updated).
- [x] **AC10:**
  - `test_naip_tile_*`: 256×256×3 PNG, metadata, bad ids, missing and inconsistent sample.
  - `test_offline_caption_run_end_to_end`, `test_caption_run_without_sample_notes_how_to_stage`.
  - Live: `naip_tile` ok in the deployment, reading the sample the runner fetched from the bucket.
- [x] **AC10b:** `scripts/stage_naip_tiles.py`.
  - It staged El Centro (footprint check: worst 5e-7°).
  - It uploaded to `s3://foundry-embeddings-data-…/samples/naip-captions/`.
- [x] **AC11:**
  - Live runs `…cap-01_3421` and `…cap-06_6d4a` (Claude Sonnet 4.6): succeeded, `naip_tile` step `images=1`, caption output (`evidence/live-captions.md`).
  - `test_cap_rebuild_keeps_images`.
- [x] **AC12:**
  - `tests/test_captions.py` retrieval tests.
  - Live: cap-01 ranked 1 of 8 (score 1.0) and cap-06 ranked 4 of 8 (0.25), evaluated in the worker with Titan V2.
  - `retrieval.json` written; judge fields null.
- [x] **AC13:** live `scripts/caption_scorer_sanity.py` (`evidence/caption-scorer-sanity.md`, re-run by the verify delegate).
  - own reference = 1.0 on 12/12;
  - the generic caption ≤ 0.5 on 12/12;
  - another tile's reference below own on 11/12.
  - Keyword-stuffed mean 0.34 (recorded only).
- [x] **AC14:** `test_every_config_has_an_embedder`, `test_hash_embedder_*`, `test_bedrock_embedder_request_and_budget`.
- [x] **AC15:** every caption prompt runs offline with `sequence matched = 1` (`test_offline_caption_every_prompt`, 12 cases).
- [x] **AC16:** the report "Retrieval" section, in tests and in the live runs.
- [x] **AC17:** `tests/test_golden.py`, `test_evaluate.py`, `test_report.py`: 38 passed. Two existing assertions changed by design (`path_checker` v2, infra S3 actions).
- [x] **AC18:** `tests/test_stac.py`: offline 9 passed, network 1 passed, against Earth Search. Assertions unchanged; the fake gained a landing page.
- [x] **AC19:**
  - `pyproject.toml` pins `rasterio==1.5.2` and `pystac-client==0.9.1`; `uv lock --check` is clean.
  - All 29 new packages are at least two weeks old.
  - `docs/requirements.md` records where each library is used.
- [x] **AC20:**
  - The image builds with the smoke check (`evidence/deploy-2.log`).
  - Worker peak 389 MB on caption runs and 699 MB on the ADB run, of 2,048 MB (CloudWatch REPORT lines).
- **Whole suite** (verify delegate): `make test` 694 passed, 0 failed; `pytest -m network` 25 passed; `make lint` clean; `make typecheck` (web TS) clean. There's no Python type checker in this repo.

**Exercised live:**
- **Deployment:** `embeddings`, sandbox 283171504459, us-west-2.
- **Caption runs:**
  - Two caption runs and their evaluations went through the real remote CLI.
  - The first two, on build `a0f8702`, failed in the deployment (`libexpat.so.1` missing from the Lambda image). That was fixed (ADR-9) and redeployed, and the re-runs succeeded.
- **AC6:** one ADB run with a crafted prompt (added through `POST /api/prompts`).
- **Local:** caption runs with Sonnet 4.6, Titan scoring, the scorer sanity check and the judge sanity check.

**Security scans** (delegate, 2026-11-03):
- **Secrets:** gitleaks 8.30.1 over the commits and the deploy log: clean.
- **SCA:**
  - OSV-Scanner 2.7.0 and Trivy 0.76.1 on `uv.lock`: the only findings are the 4 `mcp==2.1.1` advisories (one High). They are identical before and after, so pre-existing; you declined that upgrade.
  - The 29 new packages have no advisories.
- **SAST:** Semgrep CE 1.181.0: one SHA-1 warning in the hash embedder. False positive: feature hashing with no security role.
- **IaC:** Checkov 3.3.29 and Trivy config on the synthesized templates. Failed checks are identical before and after (pre-existing). The new IAM statements pass the IAM checks.
- **Image:** Trivy and Grype 0.121.0 on the Lambda image. Everything they found is pre-existing (base image, pip, RIE, uv, mcp).
- **Manual review:** the native libraries vendored in rasterio's wheel (GDAL 3.12.2, PROJ 9.8.1, libtiff 4.7.1, libcurl 8.20.0, OpenSSL 3.6.2, SQLite 3.53.1) aren't matched by scanners. GeoTIFFs are read only from our own bucket.

**Pentest probe:** not run. The change adds no inbound entry point: the new surface is outbound (signed S3 lists, the bucket sample cache, Titan calls) and a local-only MCP server. The deployed API wasn't changed except for the task list.

**docs/security/ updated:** `attack-surface.md` gained rows for the signed requester-pays lists, the `naip` MCP server, the sample cache and the text embedder.

**Gate history:**
- **Define:** self-review, no blockers.
- **Design:** 3 subagent passes.
  - **Pass 1, 10 blockers:** STAC error mapping, `matched()` order, vision model, image not visible in the trace, provenance, case-F isolation, Titan and S3 price unverified, rp-lister region, embedder budget, judge fields.
  - **Pass 2, 4 blockers:** pystac object migration (moved to raw JSON), `tiles.json` source, no cache refresh (ETags added), case-F replacements (rescoped and made plausible).
  - **Pass 3, 1 blocker:** seeded task trees never update. You decided to freeze the caption data before the first deploy (ADR-8).
  - The vision spike before the gate replaced Kimi with Claude Sonnet 4.6 (ADR-7).
- **Plan:** self-review; added commit points.
- **Verify:** subagent pass, recorded below.

**Specialists consulted:**
- **AI/ML:** scorer sanity, hard negatives, keyword-stuffing case, judge control.
- **AI safety:** caption text treated as data; tool results "data, not instructions" in `system.txt`.
- **AppSec:** scans; regex-validated tile ids; no credentials for MCP servers.
- **Platform:** IAM derived from configs; Docker smoke check; memory.
- **Data:** footprint cross-check; pinned row order.
- **QA:** tests through the real harness and CLI.

**Decisions and deviations:**
- **ADR-2:** the sample lives in each deployment's bucket (your choice), cached by the runner because MCP servers get no credentials.
- **ADR-7:** the caption agent is Claude Sonnet 4.6, not Kimi.
- **ADR-8:** caption data is frozen per deployment.
- **ADR-9:** expat is installed in the Lambda image.
- **Sample location:** El Centro (`m_3211513_se`, 2020) instead of the desert image first considered.
- **httpx:** was never a direct dependency, a design correction.
- **AC7 case design:** case F's trace isn't edited, since A's trace names none of its buckets.

**Follow-ups:**
- **Clay material (K-1):** replace the stand-in captions and retrieval set with Clay's. That needs a fresh deployment or a re-seed command (ADR-8).
- **Imported runs:** an imported run is evaluated with `config/offline.toml` (no config recorded at import), so the path check is off for imports. Record the import's `--config` (existing behaviour).
- **Re-scoring:** re-scoring from `retrieval.json` alone.
- **Image bytes in OTel spans:** check whether the OTel tracer would export image bytes before pairing `bedrock-otel` with captions.
- **Upgrades and checks:** the `mcp` 2.2.0 upgrade (declined for now); checking rasterio's vendored native-library versions against upstream advisories.
- **Sample cache on shared machines:** on a multi-user laptop the cache sits in the shared temp dir, where another user could swap files. Lambda isn't affected.
- **Scorer robustness:** a keyword-stuffed caption fully games 2 of 12 tiles. Consider a length or term-density penalty.

**Risks to watch:**
- **Cold-start latency:** the first `naip_tile` in a cold worker took 17 s (sample download plus loading rasterio), then 0.4 s.
- **AWS SSO expiry** during deploys.
- **Judge calibration:** the judge still runs on the stand-in ADB rubric.
