# Serverless AWS deployment — Handoff

**Spec:** ./spec.md   **Design:** ./design.md   **Runbook:** ../../deploy.md   **Date:** 2026-11-01   **Track:** Full

## What shipped

1. **The app runs serverless in one AWS account, us-west-2**, with no VPC, servers or file systems. It uses:
   - Lambda: api, worker and probe functions, all from one arm64 image
   - a public function URL
   - S3 and DynamoDB, which hold all state
   - Step Functions for batches
2. **The engine is unchanged.** Workers run the same `foundry` CLI on `/tmp`. A manifest-versioned mirror moves run, batch and project trees to and from the store, and jobs, locks, slots and spend are DynamoDB items. A laptop's `foundry ui` works as before.
3. **Access:**
   - **Tokens:** named, stored as hashes, managed with `cloud.sh token`. A token works for a browser sign-in (HMAC session cookie plus stateless CSRF) or as an `X-Foundry-Token` header.
   - **Limits:** sign-in is rate-limited per address (an IPv6 /64 counts as one address).
   - **Monthly spend cap:** $30 by default. Each job reserves its worst-case cost before it starts and settles to the real cost when it finishes.
4. **The web app** (slice 7):
   - sign-in, and "Signed in as … · Sign out"
   - two-step uploads, straight to the bucket in the cloud
   - edits that carry the page's project version, with a Reload banner on a conflict
   - interrupted runs shown as interrupted
   - no live receiver in the cloud
5. **A CLI with a token alone** (slice 8): `foundry-remote.pyz`, served by the deployment at `/cli/foundry-remote.pyz`. It uses only the standard library and needs Python 3.10+; from a clone it's `foundry remote`.
6. **Infrastructure and operations** (slice 9):
   - `FoundryDataStack` (retained) and `FoundryAppStack`
   - `scripts/cloud.sh`: preflight, a safe deploy (publish, check, then move alias `live`), status, tokens, seed, gc, logs, stop/start, destroy
   - `scripts/cloud_ops.py`, `scripts/cloud_smoke.py`
   - `docs/deploy.md`
7. **The sandbox deployment** (account 283171504459, us-west-2):
   - URL: **https://beoklkhu5vjfk7cxa6si53lvle0ikrhm.lambda-url.us-west-2.on.aws/** (new since the D4 drill; `scripts/cloud.sh status` prints it).
   - It holds the 77 seeded runs and 5 batches, plus my test runs.
   - The only token is `smoke`, which I used for testing. Add your own with `scripts/cloud.sh token add <name>`, and revoke `smoke` when you no longer need it.

## Decisions

All recorded in `design.md`, under "Build notes":
- **Drafting and imports stay synchronous** in the api function (300 s, 2 GB), not worker jobs. The 30 s CloudFront limit they were designed around doesn't apply, since there's no CloudFront.
- **Safe rollout through CloudFormation parameters:** each alias's version is a stack parameter that `cloud.sh` keeps in step with Lambda, so a deploy with no change changes nothing.
- **The CLI's `.pyz` is built on demand** from the installed source, not stored: Vite empties `web_dist` on every build.
- **`base_version` is optional** in requests, and the web app always sends it.
- **The api role may call Bedrock,** because drafts run inside the request (found live).
- **The worker may list the bucket,** so a missing object reads as not found (404), not access denied (403).

## Verification

**Suites:**
- **Python:** 426 passed, plus 4 `network` tests deselected. One timing test once failed because the laptop slept mid-run (the system log shows the wake at the exact second the test's step "ended"); it passes on rerun.
- **Vitest:** 34 passed.
- **Local browser e2e:** both scripts pass. `ui_e2e.mjs` now also imports a trace and drafts a rubric through the pages' two-step uploads.

**The live deployment.** Every check ran against the real sandbox:

| AC | Evidence |
|---|---|
| D1, D2 | first deploy from the checkout: 309 s, URL printed, names from stack outputs |
| D3 | a second deploy with no change: both stacks "no changes", no version published (30 s) |
| D4 | `cloud.sh destroy`: app stack gone, data stack intact; `cloud.sh deploy` (267 s) reattached it at a new URL: the same `smoke` token, all 92 runs, the batches and job records, and the operator's CLI login updated to the new URL |
| D5 | code-change deploys serving the new build in 136–184 s (8 deploys). One took 67 min: the image push to ECR stalled while the laptop was asleep and on about 200 ms round trips to us-west-2 |
| O4, rollout | a build with a broken `/healthz` failed its check, `live` stayed on the previous version, and the URL kept answering the old build |
| S5 | a live Bedrock run started on version 1 and finished after the deploy moved `live` to version 2 |
| L1 | a live Bedrock run: 17 steps seen while running, worst lag 4.7 s (limit 10 s) |
| I1 | headless Chrome against the URL: sign-in, the board (91 cards), dashboard, batches, rubric, and an import through the page's presigned S3 upload under the real CSP; no page errors |
| I2, A2–A4 | no token → 401; wrong token → 401; cookie flags `Secure`, `HttpOnly`, `SameSite=Strict`; a cookie write without CSRF → 403; a foreign Origin → 403 |
| I3, O3 | the `.pyz` downloaded with curl, `login` with a token alone, `runs --json`, `jobs` |
| I5 | 13 tools, no server errors (RODA built into the image) |
| I6 | an import through a presigned bucket POST; a real Bedrock rubric draft through the URL (3 criteria, 35 s) |
| I7 | `receiver` false in the session; the start route answers 409 |
| A1 | a revoked token refused after 59 s (limit 60 s) |
| A5 | with the month's ledger set to $29.99, a Bedrock run start → 409 "This month's spend is $29.99 of the $30.00 cap; new runs are paused until 2026-12-01" (CLI exit 1); the ledger restored to its exact value |
| A6 | wrong sign-ins → 429 after the limit |
| S2 | two edits from the same version: the second gets 409 "Changed by someone else; reload" |
| S3 | a run whose worker never ran became `interrupted` 8 s after its deadline with nothing forced, and the next run started at once and succeeded |
| S4 | `cloud.sh seed`: 77 runs, 5 batches, the project in 2 min 51 s; the board shows them |
| batches | a 2-item batch on Step Functions: finished, 2/2 succeeded |
| scoring | a live run scored in the cloud: overall 0.95 |
| S-4 | server-side: board p50 about 30 ms (limit 300); dashboard cold 2,184 ms with 86 runs (limit 3,000), warm 64–116 ms (limit 500) |
| O4, restore | **files:** deleting a run's objects didn't break it (records name exact versions); the runbook's delete-marker removal restored them. **Records:** a run's TREE item deleted, gone from the board; a point-in-time restore to a new table (about 12 min), the item copied back per the runbook, back on the board; the restore table deleted |
| X1 | synth tests: no EC2, EFS or load balancer resources |
| X2 | six idle overnight hours: the api ran 12 times an hour (the probe), the worker 0 times |
| C1 | about $1.00 a month fixed (Price List API, us-west-2): ECR $0.17, alarms $0.40 (4 alarm metrics), the probe metric $0.30, `PutMetricData` $0.09, probe compute $0.02, the rest about $0.01 |

**Bugs the live checks found** (each fixed with a regression test that fails without the fix):
1. A rubric draft got `AccessDeniedException`: the api role had no Bedrock statements.
2. A batch read "stopped, 0/2" right after finishing. Finalize finished the job before pushing `batch.json`, and the batch view read the job last.
3. Importing the same trace twice was a bare 500 (`FileExistsError`). Now it's a 409 naming the run, and in the cloud another instance's import isn't overwritten.
4. Unhandled request errors were turned into 500s without a log line. They're logged now (O1).
5. A cold API instance pulled every run one at a time (26–34 s). Pulls now run in parallel: 6.3 s for 86 runs from the laptop, 2.2 s inside Lambda.
6. A signed-out page polled `/api/jobs`.

## Gates

- **Define and Design:** signed off by you before Build (see `design.md`).
- **Verify gate:** see "Verify gate record" below.

## Deferred and follow-ups

- **A job whose invocation Lambda drops** (seen with the worker's reserved concurrency at 0) keeps the run slot for up to 32 minutes, until its deadline. The fix: an on-failure destination that finishes the job at once.
- **Not proven live:**
  - an alarm actually emailing. Confirm the SNS subscription email first; `aws cloudwatch set-alarm-state` can fire a test.
  - `--budget`
  - `stop`/`start`
- **The first full idle week's bill** (spec C1, "under $0.75"): check it in Cost Explorer a week from now.
- **ECR keeps every image version** (about 0.3 GB of new layers per changed deploy). An ECR lifecycle rule on the CDK assets repository would cap it, but that repository belongs to the CDK bootstrap, not this app.
- **My test data is in the sandbox:**
  - the smoke runs (`adb-001` with `scripted-a`, the `otel-imported-*` runs)
  - one archived smoke prompt
  - the batches `b-20261101-*`

  They're visible on the board. Remove them by hand if you want a clean demo.
- **The pentest** (the offensive security pass) didn't run: it needs your OK on its scope.
