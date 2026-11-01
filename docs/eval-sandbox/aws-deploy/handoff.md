# Serverless AWS deployment — Handoff

**Spec:** ./spec.md   **Design:** ./design.md   **Runbook:** ../../deploy.md   **Evidence:** ./evidence/   **Date:** 2026-11-01   **Track:** Full

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
   - **Monthly spend cap:** $30 by default. Each job reserves its worst-case cost before it starts and settles to the real cost when it finishes. A batch item whose worker dies is charged its worst case.
4. **The web app:**
   - sign-in, and "Signed in as … · Sign out"
   - two-step uploads, straight to the bucket in the cloud
   - edits that carry the page's project version, with a Reload banner on a conflict
   - interrupted runs shown as interrupted, and a just-started run shown as "Starting…"
   - no live receiver in the cloud
5. **A CLI with a token alone:** `foundry-remote.pyz`, served at `/cli/foundry-remote.pyz`. It uses only the standard library and needs Python 3.10+; from a clone it's `foundry remote`.
6. **Infrastructure and operations:**
   - `FoundryDataStack` (retained) and `FoundryAppStack`
   - `scripts/cloud.sh`: preflight, a safe deploy (both new versions checked before either alias moves), status, tokens, seed, gc, logs, stop/start, destroy
   - `scripts/cloud_ops.py`, `scripts/cloud_smoke.py`
   - `docs/deploy.md`
7. **The sandbox deployment** (account 283171504459, us-west-2):
   - URL: **https://beoklkhu5vjfk7cxa6si53lvle0ikrhm.lambda-url.us-west-2.on.aws/** (`scripts/cloud.sh status` prints it).
   - Build `a069d77`, both aliases on version 7.
   - It holds the 77 seeded runs and 5 batches, plus test runs (see Deferred).
   - The only token is `smoke`, which I used for testing. Add your own with `scripts/cloud.sh token add <name>`, and revoke `smoke` when you're done with it.

## Decisions

- **Spec amended at Verify, with your approval:**
  - **I6:** rubric drafting runs in the request and returns `201`.
  - **S2:** the conflict check applies to edits that send `base_version`, which the web app always does.
- **Recorded in `design.md`, under "Build notes":**
  - imports stay synchronous
  - the alias versions are CloudFormation parameters kept in step by `cloud.sh`
  - the `.pyz` is built on demand from the installed source
  - no GSI (a base-table query instead)
  - the api role may call Bedrock, because drafts run in the request
  - the worker may list the bucket, so a missing object reads as not found (404), not access denied (403)
  - probe and alarm details

## Verification

**Suites (final run, before Verify pass 2):**
- **Python:** 431 passed, plus 4 `network` tests deselected.
- **Vitest:** 37 passed.
- **Local browser e2e:** both scripts pass. `ui_e2e.mjs` also imports a trace and drafts a rubric through the pages' two-step uploads.

**The live deployment.** Every row ran against the real sandbox, and each names its raw output in `./evidence/`. **The final build, `a069d77`:** the full offline smoke (smoke-offline-a069d77.log, 15/15), the live Bedrock checks (smoke-live-a069d77.log, 4/4), the full browser drive (ui/drive2.log) and the pass-2 fixes (pass2-fixes-live.txt) all ran against it. Rows citing older logs are behaviour that build didn't change, or one-off drills.

| AC | Result | Evidence |
|---|---|---|
| D1, D2 | the first deploy from the checkout: 309 s, URL printed; names from stack outputs. The account has no default VPC in us-west-2 (`describe-vpcs`: 0) | deploy-first.log |
| D3 | a second deploy with no change: both stacks "no changes", no version published (30 s) | deploy-d3-no-change.log |
| D4 | `destroy`: the app stack is gone and the data stack intact; `deploy` reattached the data at a new URL: the same `smoke` token, 96 runs (77 of 77 seeded), every batch, the operator's CLI login updated | d4-destroy-redeploy.log, d4-s4-state.txt |
| D5 | code-change deploys serving the new build in 136–186 s (10 deploys). One took 67 min while the laptop slept mid-push and had about 200 ms round trips to us-west-2 | deploy-*.log |
| O4 | **a broken worker** (it fails its health event): neither alias moved; worker and api stayed on version 4 and `/healthz` kept answering the old build. **A broken `/healthz`** (before the fix): `live` stayed on the previous version | o4-broken-worker.log, deploy-o4-broken-healthz.log |
| O4 restore | **deleted objects:** the run kept working, and the runbook's delete-marker removal made the files current again. **A lost record:** a point-in-time restore (about 12 min), copied back, back on the board, the restore table deleted | o4-restore-drill.txt |
| O2 | the alarm metrics' dimensions confirmed from CloudWatch (`FunctionName` + `Resource=<fn>:live`, with data). A forced alarm: action executed, SNS published 1 and delivered 1 (0 failed), back to OK at the next evaluation | o2-alarms.log |
| S5 | a live Bedrock run started on version 1 and finished after the deploy moved `live` to version 2 | deploy-s5-during-run.log, smoke-live-run.log |
| L1 | live Bedrock runs: 17 steps seen while running, worst lag 5.6 s on `a069d77` (4.7 s earlier); limit 10 s | smoke-live-a069d77.log |
| I1 | headless Chrome against the URL. Passed: <br>• sign-in; New run from the dialog <br>• the run's panel, followed live to the end <br>• its Timeline, Steps, Report, Rubric and Overview tabs <br>• scoring from the panel (0.83) <br>• Compare (27 rows); prompts (11); the tool picker (13 tools) <br>• batches, dashboard <br>• a rubric draft from the page (2 criteria), then discarded <br>• an import through the page's presigned upload under the real CSP (earlier drive) <br>No page errors beyond the expected ones | ui/drive2.log, ui/*.png |
| I2, A2–A4 | no token → 401; wrong token → 401; cookie `Secure`, `HttpOnly`, `SameSite=Strict`; a cookie write without CSRF → 403; a foreign Origin → 403. A token named with dots keeps its browser session | smoke-offline-a069d77.log, pass2-fixes-live.txt |
| I3, O3 | the `.pyz` downloaded with curl, `login` with a token alone, `runs --json`, `jobs` | smoke-offline-a069d77.log |
| I5 | 13 tools, no server errors; a live run's 13 tool calls with 0 failures | smoke-offline-a069d77.log, smoke-live-a069d77.log |
| I6 | an import through a presigned bucket POST; the multipart import's run pushed to the store. A draft through the URL that took **75 s** (20 criteria, valid, $0.15) | smoke-offline-a069d77.log, pass2-fixes-live.txt, i6-long-draft.txt |
| I7 | `receiver` false in the session; the start route returns 409 (tested) | smoke-offline-a069d77.log |
| A1 | a new token worked after 1 s; the revoked token was refused after 58 s (limit 60 s) | a1-revoke.txt |
| A5 | with the month's ledger at $29.99: a Bedrock start → 409 "This month's spend is $29.99 of the $30.00 cap; new runs are paused until 2026-12-01" (CLI exit 1); the ledger restored to its exact value | a5-cap.txt |
| A6 | wrong sign-ins → 429 "Too many attempts; wait a minute" past the limit | a6-ratelimit.txt |
| S2 | two edits from the same version: the second gets 409 "Changed by someone else; reload" | smoke-offline-a069d77.log |
| S3 | runs whose worker never ran became `interrupted` at their deadline (one forced, one natural: 8 s after it), and the slot freed at once. A job that started and died frees its slot 17 minutes after its start, even for a start with no job-list read first (unit-tested: the dead-worker case can't be forced live) | s3-interrupted.txt |
| S4 | the seed (77 runs, 5 batches, the project) in 2 min 51 s; all 77 on the board | d4-s4-state.txt |
| batches | a 2-item batch on Step Functions, on the changed Finalize: finished, 2/2 succeeded | smoke-offline-a069d77.log |
| S-4 | server-side, 96 runs: board about 40 ms (limit 300); dashboard cold 2,413 ms (limit 3,000), warm 94–171 ms (limit 500) | s4-timings.txt |
| X1 | synth tests: no EC2, EFS or load balancer resources | tests/test_infra.py |
| X2 | six idle overnight hours: the api ran 12 times an hour (the probe), the worker 0 times | x2-idle.txt |
| C1 | idle about $1.00 a month (limit $3); about 500 runs a month about $5.70 at the bounded worst case (limit $10), about $1.50 typical; Bedrock limited by A5 | c1-cost.txt, c1-prices.txt, c1-usage.txt |

**Bugs found live or by the Verify passes, each fixed with a regression test that fails without the fix:**
1. A draft got `AccessDeniedException`: the api role had no Bedrock statements.
2. A batch read "stopped": Finalize finished the job before pushing `batch.json`, and the view read the job last.
3. Re-importing a trace was a bare 500. It's now a 409, and another instance's import isn't overwritten.
4. Unhandled request errors weren't logged.
5. Cold pulls ran one run at a time (26–34 s). They're now parallel.
6. A signed-out page polled `/api/jobs`.
7. The Rubric page's source tabs submitted the form (on a laptop too).
8. A just-started run showed "Run not found" for its first seconds in the cloud.
9. (Verify pass 2) A token named with dots couldn't keep a browser session.
10. (Verify pass 2) The multipart import lost its runs in the cloud.
11. (Verify pass 2) The sweep closed dead batch items at $0 before Finalize charged them.
12. (Verify pass 2) A dead job's slot stayed blocked for 32 minutes for a start with no job-list read first.

## Gates

- **Define and Design:** signed off by you before Build.
- **Verify, pass 1 (subagent): 8 blockers.** Each resolution:
  1. **S3 was 32 minutes, not 17:** a claim now sets the deadline from the start; tested.
  2. **A failed deploy could leave a mixed version live:** both versions are now checked before either moves; drilled live with a broken worker.
  3. **Dead batch items cost $0:** the per-item worst case is recorded at start and charged at Finalize; tested.
  4. **No alarm had fired:** dimensions confirmed and an alarm fired through to email delivery.
  5. **I6 differed from the spec, with no draft over 60 s:** spec amended with your approval, and a 75 s draft recorded.
  6. **I1 had pages untested:** every main path driven in the cloud, which found bugs 7 and 8.
  7. **No D4 output:** recorded.
  8. **No C1 figure for 500 runs:** priced and recorded.

  Minor findings fixed: the cap and budget survive `destroy`, and `--cap 0` sticks; log groups are retained; the runbook's region and `--output json`; a restored record's version bump; the CLI's filename escaping; regression tests for the parallel pull and for no polling before sign-in. The rest are listed under Deferred.
- **Verify, pass 2 (subagent): 5 blockers.** Each resolution:
  1. **Dotted token names:** the cookie is split from the right; tested, and checked live.
  2. **The multipart import in the cloud:** it now pushes; tested, and checked live.
  3. **Dead items closed by the sweep:** the sweep leaves batch items to Finalize; tested.
  4. **The 17-minute slot:** the slot and lock expire with the job, and a refused start sweeps and retries; tested.
  5. **The final build not smoke-tested:** every smoke and the browser drive ran on `a069d77`.

  Minor findings fixed: `spent_usd` includes the dead-item charge; authentication errors are logged; A6's concurrency limit is a deploy parameter.
- **Verify, pass 3:** see the commit that follows this handoff.

## Deferred and follow-ups

- **A job whose invocation Lambda drops** (seen with the worker's reserved concurrency at 0) holds the run slot until its 32-minute deadline. The fix: an on-failure destination that finishes the job at once.
- **IAM, state-machine and alarm changes take effect at deploy,** whatever the alias. "The previous version keeps serving" covers code and environment only.
- **Reserved concurrency** (`--concurrency`, default 20, plus 8 for the worker) and the alias moves are made by `cloud.sh`, outside the template. In an account with too little unreserved concurrency the deploy warns and the limit isn't set.
- **Stale token cache:** if the table can't be read, `TokenStore` keeps serving its last good token list for up to 10 minutes, so a token revoked just before the outage can work past A1's 60 s.
- **Header tokens and A6:** a correct header token is never rate-limited; only failures count.
- **Not driven:** the cap's refusal in the UI (the CLI path was) and revoking a browser session (unit-tested).
- **Bucket CORS** allows any origin for POST. Uploads need a presigned policy, but it could be narrowed to the URL.
- **The supply chain:** the base image isn't pinned by digest, and RODA's dependencies below the top level aren't locked. gitleaks ran on every commit with no leaks; no dependency or IaC scanner ran.
- **Tags** are `project` and `owner`, with no cost-centre tag.
- **ECR keeps every image** (about 0.3 GB per changed deploy). A lifecycle rule would belong to the CDK bootstrap's repository.
- **The first full idle week's bill** (C1's "under $0.75"): check it in Cost Explorer a week from now.
- **Not exercised live:**
  - a 50 MB import
  - `stop`/`start`
  - `--budget`
  - the pentest, which needs your OK on its scope
- **My test data is in the sandbox:**
  - the smoke runs (`adb-001` with `scripted-a`, the `otel-imported-*` runs)
  - one archived smoke prompt
  - the batches `b-20261101-*`

  Remove them by hand for a clean demo.
