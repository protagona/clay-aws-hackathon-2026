# Increment 6: hardening, S3 mirroring by default, dashboard

**Track:** Quick   **Status:** Built; deploy pending (H4)   **Date:** 2026-10-30

## Problem

- **The role can invoke any Bedrock model** (F-012), and the runs bucket keeps no access log (F-010). Both were accepted for the sandbox, with a review due before wider use.
- **Live runs stay on one laptop** unless someone edits the config to mirror them to the deployed bucket.
- **No cross-run view.** Nobody can see which model does best, at what cost, or where agents struggle, without opening runs one by one.

## Acceptance criteria

**H: hardening (F-012, F-010)**
- **H1:** The role's Bedrock statement allows `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` only on two sets of resources:
  - **Inference profiles:** `arn:aws:bedrock:us-west-2:<account>:inference-profile/<id>`, for every `us.` profile id named in `config/bedrock*.toml`: `[model]`, `[judge]`, `[summarizer]` and `[prices]` keys, the last being the batch model allowlist.
  - **Foundation models:** `arn:aws:bedrock:{us-east-1,us-east-2,us-west-2}::foundation-model/<id without "us.">`, with the condition `bedrock:InferenceProfileArn` StringLike one of those profiles. The model can be reached only through our profiles.

  The ids are read from the configs at synth time, so adding a model to a config and redeploying is enough. An id that isn't a `us.` profile fails synth with a clear message.
- **H2:** A separate access-log bucket is created: private, SSE-S3, SSL-only, RETAIN, with objects expiring after 90 days. The runs bucket sends its server access logs there under `runs-bucket/`.
- **H3:** `tests/test_infra.py` asserts the exact Bedrock resources and condition, that no Bedrock `*` resource remains, and the logging configuration. `cdk synth` passes.
- **H4:** After the user runs `cdk deploy`, `scripts/verify_deploy.sh` passes again, and a Bedrock call to a model not in the configs is denied for the role. Proved live.

**S: mirror by default**
- **S1:** `config/bedrock.toml`, `bedrock-alt.toml` and `bedrock-otel.toml` set `s3_uri = "s3://clayfoundryevalsandbox-runsbucketf47f9fb8-qedlohtzrfqy"`. Offline configs stay empty, so a fresh clone works without AWS. The README says how to point the configs at another deploy's bucket. Mirroring stays best-effort: a failed upload warns and keeps the local files (`storage.py`, unchanged).

**D: dashboard (approved design A, "Leaderboard")**
- **D1:** A new "Dashboard" tab at `GET /dashboard`, built only from `runs/*/run.json` and `scores.json`, using the same untrusted-file checks as the run cards.
- **D2, KPI row:**
  - runs
  - scored runs
  - mean overall score of scored runs
  - total spend, which is run cost from `trace.jsonl` plus evaluation cost from `scores.evaluation.cost_usd`
- **D3, leaderboard:** one row per model, sorted by mean overall (descending), with unscored models last. Columns:
  - runs
  - scored
  - succeeded/total
  - mean overall, as a bar with a min–max whisker
  - mean cost per run
  - a link to the model's runs
- **D4, cost vs score:** a dot per scored run (x = run cost, y = overall). Each model gets a categorical colour and shape, up to 3 models; further models fold into "Other" in grey. There is a legend, a hover title per dot, and each dot links to its run.
- **D5, where agents struggle:** three ranked lists, each with a bar per item:
  - tool errors by `server/tool`
  - missed sequence stages by prompt
  - output criteria by mean fraction of max, lowest first
- **D6:** Every chart has a `<details>` table view. The charts work in light and dark mode, at 375 px, and without scripts. Colours come from the dataviz reference palette, checked with its validator.
- **D7:** An empty `runs/` shows an empty state pointing at the Runs tab. Hostile values in `scores.json` and `run.json` render inert or are skipped, never a 500.

## Non-goals

- No new data files or caches; the page reads runs on each request. That's fine at hundreds of runs, so revisit only if it gets slow.
- No time-series charts (design C was not chosen), and no filters in this increment.
- No KMS (F-011 stays accepted), and no CloudTrail data events.

## Assumptions and open questions

- **Destination regions:** `us.` profiles route to us-east-1, us-east-2 and us-west-2. This was checked live with `aws bedrock get-inference-profile` for all 5 deployed profile ids on 2026-10-30. AWS requires the foundation model to be allowed in each region a profile routes to ([inference-profiles-prereq](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-prereq.html), "Important"). If AWS adds a region to a profile, calls routed there fail until the list is updated. Owner: the team. Default: keep the list and re-check on deploy.
- **Sonnet 5.5 and Haiku 5.5** appear only in offline configs, which never call Bedrock, so they aren't granted.

## Plan

**Design review:**
- **Approved by the user (2026-10-30):** option A, "Leaderboard", then the rendered page on a preview server ("Approve with tweaks"; the user then chose to move on).
- **Alternatives not chosen:** B, a model × prompt heatmap; C, score and cost over time.
- **Deviations from the wireframe:**
  - The scatter's y axis starts at the data's floor in tenths, not 0. It's a dot plot, so it needs no zero baseline.
  - The chart scrolls inside its card below 600 px, so its labels stay legible.
  - The Runs tab gained a `?model=` filter as the leaderboard's link target.
- **User feedback during review:** the user rejected the run monitor's pulsing status dot (increment 5). It's now static.

**Slices:**
1. `infra/stack.py`: Bedrock ARNs from configs, and the log bucket. Update `tests/test_infra.py` and run `cdk synth`.
2. Set `s3_uri` in the three `bedrock*` configs and update the README.
3. `ui/dashboard.py` (aggregation and SVG geometry), `dashboard.html`, the nav tab, and tests.
4. Verify: screenshots (light, dark, 375 px), the user deploys, `verify_deploy.sh`, and a denied-model probe.

## Verification (2026-10-30)

**Suite:** 223 passed. Ruff is clean, and gitleaks runs at commit. There are no new dependencies.

| AC | How it was proven |
|---|---|
| H1 | `test_bedrock_scoped_to_configured_models` checks the exact profile and model ARNs, the `InferenceProfileArn` condition, and that no `*` remains. `test_bedrock_models_rejects_unknown_id_shapes` covers the id check. `cdk diff` against the live stack shows `*` replaced by 5 profiles plus `zai.glm-5`, and 15 foundation-model ARNs under the condition. |
| H2 | `test_runs_bucket_access_logging`. The synthesized log bucket policy allows only `logging.s3.amazonaws.com` to write `runs-bucket/*`, with `aws:SourceArn` set to the runs bucket and `aws:SourceAccount` to the account. |
| H3 | **Trivy 0.76.0:** only F-011 (CMK, accepted) and F-014 (the log bucket's own logging, accepted) remain. The log bucket's missing versioning (AWS-0090) was found and fixed. **Checkov 3.3.28:** 32 passed, 1 failed, which is F-014. |
| H4 | **Pending:** the user runs `cdk deploy`, then `verify_deploy.sh` and a denied-model probe. |
| S1 | The three `bedrock*` configs carry the deployed bucket, and the offline configs stay empty. `test_storage.py`, unchanged, still proves failures are non-fatal. |
| D1–D5 | `tests/test_dashboard.py` (7 tests): KPIs, leaderboard order and spread, scatter slots following the model with folding to "Other", ranked struggles, hostile scores skipped, and the page built from real fixture runs. |
| D6 | Screenshots of the preview server with the 20 real runs: desktop light, and 375 px dark. The palette validator passes for 3 slots in both modes (all pairs); aqua is below 3:1 on white, so the relief comes from shapes, a legend and a table. |
| D7 | `test_empty_and_hostile_runs`: the empty state, a hostile model id escaped in text and URL-encoded in links, and a broken `run.json` skipped. |

**Found and fixed during build:**
- The SVG ticks shared the `.tick` class with the run monitor's absolutely positioned ticks, which displaced the labels. The SVG now uses its own classes.
- The SVG mark include was first named `.svg`, which Starlette's `select_autoescape()` does not autoescape. It was renamed to `.html`.
- CDK refused log delivery through an ACL on a bucket with ACLs disabled. The fix is the documented flag `@aws-cdk/aws-s3:serverAccessLogsUseBucketPolicy`, set on the stack.
