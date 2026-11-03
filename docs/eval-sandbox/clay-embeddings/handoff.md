# Clay embeddings and path checks: Handoff

**What shipped:**

- **The path check.** During evaluation, every S3 location an agent's plan cites is listed once, anonymously, and marked `found`, `not_found` or `unverified`. The judge sees the results in a trusted block outside the run data, and they land in `scores.json` and `report.md`.
- **A third MCP server.** It gives the agent Clay v1.5 embeddings (LGND, Sentinel-2) through three tools: coverage, similar places and change.
- **Two ADB prompts** (`adb-007`, `adb-008`) whose expected workflows use the server.
- **The prototype's first live check of Clay's own product:** an agent evaluated on how it uses Clay's embeddings.

Spec: [spec.md](spec.md) · design: [design.md](design.md) · evidence: [evidence/](evidence/).

## Verification

**Path check**

- [x] **AC1** (forms, `paths` in `scores.json`): `tests/test_paths.py::test_extract_forms` and `test_evaluate_writes_paths_and_judge_sees_block`.
- [x] **AC2** (status mapping): `test_status_mapping`, 9 cases through the real `default_lister` with the boto3 client stubbed. Includes the empty bucket counted as `found`.
- [x] **AC3** (unsigned, list only): `test_lister_is_unsigned_list_only`. A manual probe in the security scan blocked every send: crafted buckets (`evil.com`, `169.254.169.254`, …) all resolved to `https://s3.amazonaws.com/<bucket>`.
- [x] **AC4** (limit, timeouts, budget): `test_limit`, `test_budget_does_not_wait_for_hung_requests` (0.3 s budget against a 5 s hang returns in under 2 s), and the client config asserted in AC3's test.
- [x] **AC5** (evaluator block):
  - The block sits after `</run_data>` in the prompt the judge received (`test_evaluate_writes_paths_and_judge_sees_block`).
  - A forged block in the agent's output is neutralised (`test_forged_block_neutralised`).
  - Live: the judge's feasibility rationale for adb-007 reads "sentinel-cogs, confirmed by evaluator".
- [x] **AC6** (report table, escaping): `test_paths_table_escaped` (a key with `|` and `<script>`), plus `report.md` in the evaluate test.
- [x] **AC7** (golden run, real S3): `test_golden_paths_network`.
  - The Clay Sentinel-2 path that the stored judge rationale called "fabricated" is `found`.
  - `usgs-landsat` is `unverified (access denied (Requester Pays))`.
  - A made-up bucket is `not_found (bucket does not exist)`.
- [x] **AC8** (switching the check off):
  - `test_config_off`, `test_enabled_env_and_config` and `test_offline_configs_off`.
  - Verify run: `foundry evaluate` with `offline.toml` writes `"paths": null`.
  - The conftest guard fails any non-network test that tries real S3.

**Embeddings server**

- [x] **AC9** (registration, three tools):
  - `test_registered_everywhere_stac_is` checks all 8 configs.
  - `test_stdio_lists_three_tools` goes through the real stdio entry point.
  - `test_tools_lists_embeddings_tools` goes through `foundry tools`.
- [x] **AC10** (coverage): `test_coverage_lists_months_and_gaps` (local fixture) and `test_live_coverage` (real bucket: Fresno, first month 2017-01, 2024-06 missing).
- [x] **AC11** (similar places): `test_similar_places_ranks_lookalikes`. Live on adb-008: 4.9 s.
- [x] **AC12** (change): `test_embedding_change_finds_the_changed_cell` and `test_change_over_empty_area`. Live on adb-007: 27.4 s, then 15.6 s.
- [x] **AC13** (errors): `test_errors` (11 cases), `test_four_partitions_refused`, and the verify-run edges (NaN, `2024-13`, `k=0`, strings).
- [x] **AC14** (result size): `test_result_size_worst_case` (`k=25` and a full change, each under 8,000 characters). Real 10° coverage boxes over 6 partitions (California, Nepal and India, Amazon) returned 989, 1,917 and 2,246 characters.
- [x] **AC15** (time and memory in the worker), measured live in the `embeddings` deployment:
  - Every embeddings call finished in under 60 s: coverage 0.5–0.8 s, `similar_places` 4.9 s, `embedding_change` 27.4 s and then 15.6 s.
  - Worker max memory: 1,741 MB with DuckDB at 1 GB, then 1,688 MB at 768 MB. Both are under 1,800 MB, of 2,048.
  - The 20 s target was relaxed to 60 s under the rule the user agreed before the run.
- [x] **AC16** (no runtime download): `evidence/deploy.log`, line 126, shows `/opt/duckdb-extensions/v1.5.6/linux_arm64/httpfs.duckdb_extension` installed at image build. The server turns autoinstall off when that directory exists.

**Prompts and workflow**

- [x] **AC17** (prompts and expected stages): `tests/test_tasks.py::test_expected_and_scripts_cover_every_prompt` and `test_embeddings_prompts_expect_embeddings_tools`.
- [x] **AC18** (system prompt): `test_embeddings_prompts_expect_embeddings_tools`.
- [x] **AC19** (suite, lint, offline runs):
  - `make test`: 642 passed, 12 skipped.
  - `make lint` and `make typecheck` are clean.
  - `test_offline_run_every_prompt` runs all 8 prompts through the real CLI (network).
- [x] **AC20** (live Bedrock runs): one each on adb-007 and adb-008 in a fresh named deployment, both `succeeded`, both evaluated with the path check on (`evidence/live-adb-007.md`, `evidence/live-adb-008.md`).

## Exercised live

- **Deployment:** named deployment `embeddings` in the sandbox account (us-west-2), deployed five times. The URL is in `evidence/deploy*.log`.
- **adb-007 (Kimi K3):** coverage, `embedding_change` (27.4 s), then `get_item` / `search_items` on the scene ids it returned, then the plan.
  - Evaluated: overall 1.00, path check 2/2 found, sequence 2/2.
  - Rerun on the 768 MB build: `embedding_change` took 15.6 s and peak memory was 1,688 MB.
- **adb-008:** coverage, `similar_places` (4.9 s), then STAC. Overall 1.00, path check 2/2 found.
- **Found and fixed live:**
  - The Lambda base image has no `find` (Dockerfile fix).
  - A seeding bug that existed before this change: a worker that cold-started first had no `tasks/`. Fixed in `cloud_boot.py` with a regression test.
  - Memory headroom (DuckDB limit lowered to 768 MB).
- **Deploys:** builds 47067d7 (`deploy-4.log`) and 2d69274 (`deploy-5.log`) went live, then 8d57954 (`deploy-6.log`, the final commit) after an AWS SSO re-login. The live AC20 evaluations ran on the path-check code from before the character allowlist; AC7 was rerun against real S3 afterwards.

## Security scans

- **Secrets:** gitleaks 8.30.1 over the commit range and the evidence folder: clean. Trivy 0.75.0 fs secret scan: two false positives in a third-party docstring inside `.venv`.
- **Dependencies:** OSV-Scanner 2.6.0 and Trivy 0.75.0.
  - `duckdb==1.5.6` has no known vulnerabilities.
  - `mcp==2.1.1` has four advisories (one high), fixed in 2.2.0. That pin predates this change; see Follow-ups.
- **SAST:** Semgrep CE 1.180.0 (`p/python`, `p/security-audit`, `p/secrets`, `p/dockerfile`) over every changed source file: clean.
- **Dockerfile:** Trivy config and Checkov 3.3.27. Only findings from before this change, plus false positives for Lambda images (root user, no HEALTHCHECK). The new httpfs line wasn't flagged.
- **Container image:** unscanned, because no image was built locally. The httpfs binary is protected only by DuckDB's extension signature check.
- **Manual review:**
  - All SQL values from tools are bound parameters; f-strings only insert constants.
  - `paths.py` can only reach S3 endpoints.
  - Accepted low risk: a model can make the evaluator list a bucket the attacker owns, which reveals our egress IP and the listed prefix in that bucket's access logs (noted in `docs/security/attack-surface.md`).

**Pentest probe:** not run. The change adds no inbound entry point, only outbound requests from the MCP server and the evaluator. Not probed: the deployed API, which this change doesn't touch.

## Gate history

- **Define:** self-review, 1 pass. Ground truth changed the spec before the gate closed: AC7 flipped once we found the judge's "fabricated" path exists, and Clay's NAIP embeddings were found, correcting commit 8fd17ef.
- **Design:** subagent review, 3 passes (16, then 4, then 3 blockers). Fixed:
  - test env propagation into subprocesses;
  - the botocore retry semantics;
  - executor shutdown;
  - concurrent tool calls (a lock);
  - httpfs in tests (skipped for local roots);
  - `http_timeout` units (seconds, not milliseconds);
  - the evaluator block's trust boundary;
  - the expected-stage semantics;
  - the scene ids (verified against Earth Search).

  The remaining trade-off (slow Lambda reads) went to the user, who chose: measure, then relax AC15 to 60 s if needed.
- **Plan:** self-review.
- **Verify:** subagent review.
  - Pass 1: one blocker. Markdown links, autolinks and `;`-joined paths gave garbled `not_found` locations; fixed in the regex, with tests.
  - Its questions are answered under Risks to watch (two-partition worst case, approximate timeout) and in AC14 (coverage size measured).
  - The report wording now names both ways to turn the check off.
  - Pass 2: em dashes, query strings, fragments, `,` lists, emphasis, `?`/`!`, non-ASCII punctuation and `%s` still garbled keys. Fixed by switching to the allowlist (16 cases in `test_extract_stops_at_anything_unsafe`).
  - After the change, `pytest -m network` passed: 24 tests, including AC7 against real S3 and AC19's offline run of every prompt. One existing test that pinned the server list was updated to include `embeddings`.
  - Pass 3 (the last allowed): one blocker. An imitation `<evaluator_checks source=…>` tag with attributes wasn't neutralised. Fixed: the tag pattern now matches attributes, with a test. China-partition hosts (`.amazonaws.com.cn`) no longer leak `.cn` into the key.
  - That last fix was not re-reviewed independently, because the gate's three passes were used up. Probed in pass 3: about 85 realistic citation forms, and no correct citation comes out longer than the real path.

## Specialists consulted

- **AI/ML and AI safety:** the evaluator block sits outside the untrusted data; tool results are treated as data.
- **AppSec:** the scan above.
- **QA:** only S3 and the model are faked; the real entry points are tested.
- **Platform and SRE:** timeouts, the memory limit, extension packaging, the seeding fix.
- **Offensive security:** limited to the attack-surface rows and the endpoint probe.

## Decisions and deviations

- **Spec AC15 relaxed to 60 s** under the pre-agreed rule.
- **DuckDB memory limit lowered to 768 MB** (design said 1 GB).
- **`enable_progress_bar`** is set with `SET` after connecting, because 1.5.6 rejects it as a connect option.
- **Path-check keys keep only `A-Z a-z 0-9 . _ - / =`;** any other character ends the key. Cutting early is harmless, because a shorter prefix of a real path still lists as `found`. Cutting late turns real locations into `not_found`. This replaced a list of placeholder characters to cut at, after the verify run and two Verify passes kept finding garbled citations: tables, links, autolinks, `;` and `,` lists, em dashes, query strings, fragments, emphasis, `%s`. A bbox of strings is now a tool error.
- **`cloud_boot.py` seeding fix:** outside this feature's scope, but it blocked AC20. It's a one-line fix with a regression test.
- **The judge input** gains the `<evaluator_checks>` block, while `templates.judge` stays `judge-v1`. `path_checker` marks runs that carried it.

## Follow-ups

- **Bump `mcp` 2.1.1 to 2.2.0** for four advisories that predate this change. It needs its own test pass, because the MCP SDK drives every server.
- **Scan the built image** with Trivy or Grype.
- **The judge scored both live runs 1.00** with the stand-in rubric. Rerun `scripts/judge_sanity.py` with the path check on, and calibrate again once Clay's rubric arrives.
- **Caption task:** use `clay-v1-5-naip-2` once Clay confirms which embeddings set (K-1).
- **More citation forms:** GDAL's `/vsis3/bucket/key`, which is common in geospatial plans, plus `s3a://`, dualstack and FIPS hosts. These are missed today (not garbled), so they show as uncited.
- **Faster embeddings:** pre-extract areas, or cache partitions in `/tmp` across calls, if `embedding_change` across two partitions proves slow.

## Risks to watch

- **Two-partition worst case not measured live:** both prompts fall inside one partition. A two-partition `embedding_change` reads 4 partition-months, about twice the data, with DuckDB spilling past 768 MB. Accepted for the hackathon. If it's slow or runs out of memory, the agent gets a tool error and can shrink the box.
- **The 60 s timeout is approximate:** the interrupt lands after the HTTP read in flight, and calls queue on the lock, so one call can exceed 60 s. The run's own `max_seconds` (300 s) is the hard stop.
- **Worker memory headroom:** about 360 MB at peak. A change query across two partitions reads twice the data, and DuckDB spills to `/tmp` past 768 MB, which is slower. Watch `Max Memory Used` in the worker's `REPORT` lines.
- **source.coop layout changes** would surface as tool errors naming the path.
- **On laptops,** embeddings queries take 30–110 s per partition, and the first call downloads httpfs.
