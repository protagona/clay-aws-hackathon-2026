# Running the eval sandbox in AWS, serverless: public web UI, API and CLI — Spec

**Track:** Full (a public surface, an auth model, cloud state)   **Date:** 2026-10-31   **Status:** Signed off by the user, 2026-10-31

## Problem

The sandbox only works on the laptop it was cloned to.
- **No remote access:** nobody can open its UI, call its API or use its CLI from anywhere else.
- **One disk of record:** its runs, prompts and rubrics live on that disk alone.
- **One laptop to run on:** real use means keeping that laptop awake.

**Who has it:**
- **Now:** the builder, who wants to develop and use it as a hosted service.
- **For the hackathon:** whoever deploys it into the event's AWS account, plus Clay's engineers and the judges who use it there. Requirement M8: "`deploy` from a clean checkout works; … Clay can access it after the event."

**Evidence** (2026-10-31, from the code):
- **Local only:**
  - `foundry ui` binds `127.0.0.1`.
  - The Host allowlist accepts only loopback names.
  - Origin is compared with `http://{host}`.
- **State on local disk:** runs (`runs/`), prompts, expected steps, rubrics, rubric history and drafts (`tasks/`).
- **Work as subprocesses of the server process:** runs, scorings, batches and tool listings, tracked in memory, with PID-liveness locks.
- **Tied to this account:** the RODA MCP server is fetched by `uvx` at run start, and `config/bedrock*.toml` hard-code this account's bucket.
- **Data to move:** 77 run directories (4.9 MB) and `tasks/` (88 KB).
- **The account has nothing to build on:** no hosted zone and no certificate. Its account-wide budget is $100 a month.

**User decisions (2026-10-31):**
1. **Three public interfaces:** a web UI, the API, and a CLI-style tool. All are usable publicly, with **no AWS identity** needed to use them.
2. **No domain.**
3. **A spend cap:** yes.
4. **Scope:** this deployment is the builder's development environment. The hackathon deployment goes to a **different account**, so it must be easy to deploy from the repo, quick to iterate on, and usable.
5. **The repo stays private.**
6. **Serverless, with no VPC-bound services and no EFS** (chosen after a documented survey of AWS options):
   - App Runner is closed to new customers since 2026-04-30.
   - ECS Express Mode needs a default VPC, which Control Tower accounts lack.

## Users and jobs

| User | Job | Has |
|---|---|---|
| Builder | deploy, change code, redeploy, use it daily | the repo, AWS credentials for **deploying** |
| Operator in another account (the hackathon deploy) | deploy from the repo into a fresh account with nothing hand-edited | AWS credentials, Docker or Finch, Node, uv |
| User (Clay engineer, judge, teammate) | open the UI, start and compare runs, script it from a terminal | a browser, or a shell with Python 3.10+ (the CLI is downloaded from the deployment), and an **access token** from the operator. No AWS account, no repo access. |

## Acceptance criteria

**Shape (X1–X2)**
- **X1: no VPC.** The deployed system creates no VPC, subnets, security groups, NAT, load balancer or file system, and uses none.
- **X2: idle costs nothing in compute.** With no traffic, no compute runs. Lambda, DynamoDB on-demand, S3 and Step Functions are all billed per use.

**Deploy anywhere (D1–D5)**
- **D1: one command from a clean checkout, into any account.**
  - `scripts/cloud.sh deploy --email <alerts>` creates everything in the deploying credentials' account, in **us-west-2**. That's where the configs' Bedrock profiles and the IAM policy live, and next to NAIP. Other regions are a non-goal.
  - Nothing names a fixed account, bucket or VPC. It works in accounts with no default VPC.
  - The only steps outside the script are `cdk bootstrap` once per account, and Bedrock model access in the console.
- **D2: the app finds its own resources.** Bucket and table names come from the stack, and no config file is edited by hand. The URL is printed at the end of the deploy.
- **D3: a second deploy with no code change changes nothing.**
- **D4: tear-down keeps the data, and a redeploy reattaches it.**
  - `scripts/cloud.sh destroy` removes the functions and workflows, and leaves the data in place: the state bucket, the tables and the token store.
  - The next deploy serves the same runs, prompts and tokens.
  - The URL changes after a destroy, and that's accepted. The deploy prints the new one and updates the operator's saved CLI login. Other users get the new URL from the operator; their sessions and CLI logins at the old URL stop working. A stable URL needs a custom domain, a non-goal.
- **D5: iteration is quick.** After a code change, `scripts/cloud.sh deploy` has the new code serving in **at most 5 minutes**.
  - Measured from the command's start until `/healthz` answers with the new build id. `cloud.sh` prints the time; the builder records it in the handoff at Verify.

**Three public interfaces (I1–I7)**
- **I1: web UI.** Any browser can open the deployment's HTTPS URL, a Lambda function URL `https://<id>.lambda-url.us-west-2.on.aws/`.
  - Without a valid token it shows only a sign-in page.
  - After sign-in, every page works as it does locally: board, run detail with its live feed, scoring, batches, Compare, dashboard, prompts, the tool picker, and rubric import and drafting.
- **I2: API.**
  - Every `/api` route answers with an `X-Foundry-Token: <token>` header, with responses that match the API contract (`src/foundry/api/schemas.py`) exactly as locally. The only differences are listed in I6.
  - Without a valid token it returns **401** `{"error": "Sign in required"}`. It never returns data, and never 200 for a write.
- **I3: CLI.**
  - **Served by the deployment itself,** so users need no repo access (the repo is private) and no AWS account. `GET /cli/foundry-remote.pyz` downloads a single-file, standard-library-only Python 3.10+ program, with no token needed: it holds no secrets.
  - Install: `curl -O <url>/cli/foundry-remote.pyz`, then `python3 foundry-remote.pyz login <url> <token>`. The builder can also install it from a clone, as `foundry remote`.
  - `login` saves the URL and token.
  - It can then list runs, start a run, start scoring, start and watch a batch, show a run, compare two runs, import a trace file, and list jobs.
  - Text by default, `--json` for raw. Exit code is non-zero on API errors, with the message.
- **I4: the laptop works as today, with the two contract changes in I6.** `foundry ui` on 127.0.0.1 needs no token, keeps state in local files, and runs jobs as local subprocesses. The offline configs and the existing tests behave as before, except where they exercise those two changes (updated with them).
- **I5: runs need no download at run time.**
  - The RODA MCP server (0.1.7) is built into the image.
  - A live Bedrock run in the cloud lists RODA's and STAC's tools (13 today) and completes.
- **I6: the complete list of differences.** Each is in the API contract.
  - **Contract changes, the same locally and in the cloud:**
    - **Uploads are two-step.** `POST /api/uploads` returns an upload URL and form fields; the client POSTs the file there, and the import or draft call names the upload. In the cloud the URL is presigned for the state bucket (at most 15 minutes, one object); locally it's a route on the same server. Uploads are up to 50 MB, as today.
    - **Rubric drafting is a job.** `POST /api/rubric/drafts` returns `202 {draft_id, job_id}`, and the page polls until the draft is ready.
  - **Present only when auth is on (cloud):**
    - `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`
    - 401 without credentials
    - 429 past the rate limit (A6)
    - 500 when the token store is unavailable
  - **Present only when a cap is set (cloud by default):** 409 past the spend cap (A5).
  - **Rare, cloud only:** 409 "Busy; try again" when a start keeps colliding with other starts on the shared spend or batch records after 5 retries.
  - **Present only when the receiver is off (cloud):** 409 on its start endpoint (I7).
- **I7: the live trace receiver is off in the cloud.** Its start endpoint answers 409 "Not available in this deployment; import trace files instead".

**Access control (A1–A6)**
- **A1: tokens.** Each user has a named access token.
  - The operator manages tokens with `scripts/cloud.sh token add <name>` (which prints the token once), `token list` and `token revoke <name>`.
  - Tokens are stored only as hashes, in the deployment's own store.
  - A revoked token stops working within 60 seconds, for header-token requests and for browser sessions made with it. Each request checks against a token list cached for at most 60 s per instance.
- **A2: the browser session.**
  - Signing in exchanges the token for an `HttpOnly`, `Secure`, `SameSite=Strict` session cookie, valid for 12 hours or until the token is revoked.
  - The token never sits in browser storage.
  - Browser writes still need the CSRF token, and an Origin equal to the deployment's URL.
- **A3: one front door.** The function URL is the only public endpoint.
  - Workers, workflows, tables and the bucket have no public access, except an upload URL presigned for one object, for 15 minutes or less.
  - The app accepts the function URL's hostname as Host, plus the loopback names (for local use).
- **A4: no secrets in the repo or the image.** Functions get Bedrock, S3 and DynamoDB rights from their roles, least-privilege like today's `HarnessRole`.
- **A5: a monthly spend cap.**
  - The cloud app refuses new runs, scorings, batches and rubric drafts once this month's **spend plus reservations** would cross `cap_usd`.
  - The cap is a deploy parameter, default **$30**.
  - The refusal is a 409 with the spend, the cap and the reset date, shown in the UI and the CLI.
  - **Spend** is a ledger: every job adds its recorded cost, when it finishes, under the UTC month it **started** in: the month whose cap allowed it. Re-scoring adds again, and earlier costs aren't overwritten.
  - A job that ends without a recorded cost (a crash, a timeout, an unpriced model) is charged its reservation.
  - **In-flight work reserves its computed worst case,** checked and reserved atomically:
    - **a run:** `max_input_tokens` × input price + `max_model_calls` × `max_tokens` × output price, from its config. Today about $1.87.
    - **a scoring:** the judge's and summarizer's worst case, computed the same way.
    - **a batch:** `max_cost + concurrency × (run_wc + eval_wc) − spent_so_far`. Here `run_wc` is one run's worst case, `eval_wc` one scoring's (0 when the batch doesn't score), and `spent_so_far` is what the batch has already booked.
      - The batch's real costs count only inside this reservation while it runs, never on top of it.
      - When it ends, the reservation is replaced by its actual total. The `concurrency` term covers jobs started just before the batch reached its own cap, which it checks only before each job.
      - **Worked example:** with `max_cost` $2, concurrency 2, `run_wc` $1.87 and `eval_wc` $0.30, the batch reserves 2 + 2 × 2.17 = **$6.34** at start. Under a $30 cap with nothing else spent, it starts.
    - **a draft:** the drafting model's worst case.
  - A configuration whose model has no price can't be started while a cap is set.
  - Locally there's no cap unless one is set.
- **A6: brute force.**
  - Tokens carry 256 random bits.
  - Sign-in attempts and failed token checks are limited to 10 a minute per client source IP, the one the function URL reports, which the client can't set. Beyond that, the answer is 429.
  - The counters are shared by every instance, in the deployment's own table, so a client can't dodge the limit by landing on another instance.
  - A function-level concurrency limit bounds total load (a deploy parameter, default 20).

**State (S1–S5)**
- **S1: the state store is the system of record.** S3 plus DynamoDB, never a function's local disk. Runs, prompts, expected steps, rubrics, rubric history and drafts survive any redeploy, cold start or stack update.
- **S2: no lost updates.** Two concurrent edits to the same prompt, rubric draft or history can't both silently win. The second gets 409 "Changed by someone else; reload".
- **S3: no stuck locks.** Runs, scorings, drafts and tool listings each run as one Lambda invocation, which is capped at 15 minutes. Batches run as a Step Functions workflow (no time cap that matters here).
  - A lock or job record left by an invocation that crashed or timed out stops blocking within **17 minutes** of the job starting: the 15-minute invocation cap plus a 2-minute margin. The cleanup happens at the next read that sees it, so that read is when it shows as interrupted.
- **S4: seeding.** One documented command copies a local `runs/` and `tasks/` into an **empty** deployment, and the board shows them. If the deployment already holds a run, the seed is skipped as a whole and the command says so.
- **S5: deploys don't interrupt work.**
  - Runs, scorings and batches already started keep running to completion through a deploy.
  - A job that dies anyway (a function timeout or a crash) shows as **interrupted** within 17 minutes of starting (S3). It can be scored from the UI and the CLI if it recorded a trace.

**Live behaviour (L1)**
- **L1: live runs stay live.** The board and the run panel of a running run show its new steps within 10 seconds. The UI polls in the cloud as it does locally, with no streaming, and that's acceptable. "Live feed" in I1 means this.

**Operate (O1–O4)**
- **O1: logs.** Function and workflow logs reach CloudWatch Logs with 30-day retention: request errors, the `foundry.api` contract log, job starts and exits, run logs.
- **O2: alerts by email.**
  - When the API function's error rate exceeds 10% over 15 minutes, or a probe of the public `/healthz` fails twice in 10 minutes.
  - The probe is a small scheduled function, inside C1's idle budget. `/healthz` needs no token and returns no data.
  - **Budget (optional):** an account-wide monthly budget alert with `--budget <usd>`. Tag filters don't work in an organization member account. The hard limit on variable spend is A5's cap.
- **O3: the CLI covers jobs too.** Batches and other long jobs are started and watched through the API (I3).
- **O4: rollback and recovery.**
  - A failed deploy leaves the previous version serving.
  - Rollback is a redeploy of the previous commit.
  - The state bucket is versioned, and the tables have point-in-time recovery (35 days).
  - A restore procedure is written, and run once at Verify: restore one run's files from a previous version, and the board shows it again.

**Cost (C1)**
- **C1: fixed monthly cost.** Checked at Verify by re-pricing from the AWS Price List API (us-west-2), and recorded in the handoff:
  - **Idle:** at most **$3 a month** before Bedrock (storage, logs, the probe).
  - **About 500 runs a month:** at most **$10** before Bedrock.
  - **Bedrock** is limited by A5.

## Success signals

- **Fresh-account deploy:** in an empty account with no default VPC, following only `docs/deploy.md`, the deploy, a token, a browser sign-in, a live run and its score in the UI all take under 30 minutes.
- **CLI from another machine, with no clone:** `curl -O <url>/cli/foundry-remote.pyz`, then `python3 foundry-remote.pyz run start --wait` and `… runs` work, with a token alone.
- **Iteration:** the code-change-to-serving time stays at most 5 minutes over the first week.
- **Idle cost:** the first full idle week's billing is under $0.75, a quarter of C1's monthly figure.

## Non-goals

- A custom domain, CloudFront or a WAF. Possible later, as parameters.
- Per-user permissions or multiple workspaces. Every signed-in user is equal, like today.
- Exposing the OTLP receiver.
- CI/CD pipelines. Deploys run from a laptop.
- Regions other than us-west-2.
- Making the repo public.
- Who owns the hackathon account and its spend after the event. That belongs to that account's owner.
- Changing the harness, scoring, importer or tool listing. They run unchanged, in a different place.
  - **Batches are the stated exception:** in the cloud, the loop that runs a batch's jobs (today a thread pool in `engine/batch.py`) is replaced by a workflow, because a batch outlives one function's 15 minutes.
  - It reuses the engine's job expansion, derived configs and summary rows, and keeps the same rules: per-job `max_cost` check, `skipped (budget)`, the `summary.csv` columns.
  - Locally, `engine/batch.py` runs as today.

## Open questions

None blocking. Owners and defaults for the rest:
- **The cap:** builder; default $30 a month.
- **The concurrency limit:** builder; default 20.
- **The alert email:** builder; required at deploy.

## Assumptions

- **AS1:** Bedrock model access and quotas are granted per account, by hand. One live run after deploy proves them (verify by calling).
- **AS2:** traffic is a handful of people, and a run is bounded at 300 s and 12 model calls.
- **AS3:** the deploying machine can build an `arm64` image (Docker or Finch), checked by `cloud.sh`.
- **AS4:** these come from official AWS docs, 2026-10-31, and are proved by spikes before Build depends on them:
  - Lambda allows subprocesses and a read-only image with a writable `/tmp`, and freezes after the response.
  - Function URLs give HTTPS on `*.lambda-url.<region>.on.aws`, with 6 MB buffered request and response bodies.
  - Requests run up to the function timeout (unverified for over 60 s).
  - S3 supports conditional writes.
  - A Step Functions Standard workflow can run for hours.
  - Its inline Map state runs up to 40 iterations at once, comfortably above the app's own batch concurrency limit of 4.
- **AS5:** the target account's organization doesn't block public function URLs (`AuthType NONE`) with a service control policy. If it does, the deploy fails visibly, and `docs/deploy.md` names the policy to ask for. The hackathon account hasn't been checked.
