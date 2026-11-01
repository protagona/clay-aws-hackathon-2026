# Running the eval sandbox in AWS, serverless — Design

**Spec:** ./spec.md   **Date:** 2026-10-31   **Status:** Signed off by the user, 2026-10-31.
- **Approved:** the design, including the resolutions of review pass 3's three blockers, and both defaults: a failed batch item doesn't stop its batch; the restore window is 35 days.
- **Authorized:** the builder may deploy to the sandbox account (283171504459, us-west-2, profile `protagona-sandbox-1`).
**Supersedes:** ./design-fargate-superseded.md (its gate record stays as evidence)

## The shape

```
browser / foundry-remote.pyz / curl
        │ HTTPS  https://<id>.lambda-url.us-west-2.on.aws/        (no VPC anywhere)
        ▼
  ┌─ api function (ARM64 image, 1 GB, 60 s) ──────────────────────────────────────────────────────┐
  │  foundry.lambda_api: function-URL event (payload 2.0) → ASGI → the FastAPI app                  │
  │  cloud backends: state via the mirror (manifests), jobs/locks/spend/tokens via DynamoDB         │
  └───────┬───────────────────────────┬───────────────────────────┬──────────────────────────────────┘
          │ async Invoke              │ StartExecution            │
          ▼                           ▼                           ▼
  worker function (same image,   batch state machine (Standard)   DynamoDB "foundry" (on-demand, PITR)
  2 GB, 900 s; event age 15 min, Prepare → Map{claim → run →      S3 state bucket (versioned):
  retries 0): run · evaluate ·   eval? → row} → Finalize;           runs/<id>/* · runs/.batches/<id>/* ·
  draft · tools · import ·       Catch → Finalize(failed)           project/tasks/* · files/* · jobs/*.log ·
  batch item; engine unchanged                                     uploads/*
  on /tmp, pushed with manifests
  probe function (5 min) → /healthz · alarms: api errors, probe, api invocations → SNS email
```

## Decisions (ADRs)

**ADR-1: Lambda, S3, DynamoDB and Step Functions** (the user's choice after the documented survey).

**ADR-2: the engine is unchanged; a sync layer and cloud backends move its state.** "Services unchanged" was too strong (pass 1). What actually holds:
- **The engine** (harness, scoring, importer, tool listing, `engine.batch`'s expansion and `summary_row`) runs unchanged, in a worker, on `/tmp/state`.
- **`foundry.data` gains cloud backends** for the state that today is either a file or in memory:

| State | Today | Cloud |
|---|---|---|
| run, project and file trees | files | the mirror (ADR-5), with manifests |
| jobs (`ws.jobs`) | in memory | `JOB` items (ADR-6) |
| locks (`.evaluating`, PID liveness) | files | `LOCK` items with leases |
| spend ledger | none | `SPEND` items (ADR-8) |
| tokens and sessions | none | `TOKEN` and `CONFIG` items (ADR-9) |
| CSRF token | per process | stateless HMAC (ADR-9) |

- **Services change where they read in-memory state directly**, and must go through the backend: `prompts.pending`, `batches.batch_view`, `tools.tool_menu`, `job.prompts`, `runs.board`, `runs.cards` (the evaluating headline). Each is listed under "App changes".

**ADR-3: our own function-URL-to-ASGI adapter, `foundry/lambda_api.py`.**
- **Rejected:** Lambda Web Adapter (an unsupported `awslabs` project) and Mangum (third party).
- **The adapter maps** payload format 2.0 (urls-invocation.html) onto ASGI:
  - `rawPath` and `rawQueryString`
  - `headers`: comma-joined multi-values
  - `cookies`: an array, joined into a single `Cookie` header
  - the body, base64 when `isBase64Encoded`
  - `requestContext.http.sourceIp` as the ASGI client
  - `requestContext.domainName` as the trusted public host
- **The response** maps back: multiple `Set-Cookie` headers go into the `cookies` array; binary bodies are base64.
- **Host and Origin (pass 1 #15):** no IAM self-lookup. The guard treats `requestContext.domainName`, which AWS sets and the client can't, as the allowed Host, and `https://<domainName>` as the allowed Origin. So there's no `PUBLIC_URL` environment variable and no CDK cycle.

**ADR-4: one DynamoDB table, `foundry`.** On-demand, PITR on, TTL attribute `ttl` (used only on `RATE` and `UPLOAD` items).

| pk | sk | Attributes | Notes |
|---|---|---|---|
| `TREE` | `run#<id>`, `batch#<id>`, `project#<task>`, `file#tools` | `version`, `manifest` (key → etag, size), `card` (runs only), `summary` (runs: `overall`, `rubric_sha256`, `spent`, `task_id`, `prompt_id`, `started_at`) | **no TTL ever.** Versions never reset (pass 1 #11). |
| `JOB` | `<job_id>` | `kind`, `run_id`, `parent` (batch), `label`, `status` (`queued`, `running`, `done`, `failed`, `interrupted`), `exit_code`, `started_at`, `finished_at`, `deadline` (not set for batches), `execution_arn` (batches), `dismissed`, `prompt_ids`, `reservation`, `month` (start month), `spent` (batches) | queried through a GSI `status-index` (pk `status`, sk `started_at`) |
| `SLOT` | `ui-run`, `batch` | `job_id`, `expires` | the "one at a time" rules (pass 1 #10) |
| `LOCK` | `eval#<run_id>`, `lease#project#<task>`, `lease#batch#<id>` | `owner`, `expires` | conditional put: `attribute_not_exists(pk) OR expires < :now` |
| `SPEND` | `<YYYY-MM>` | `committed` | ADR-8 |
| `TOKEN` | `<name>` | `sha256`, `created_at` | |
| `CONFIG` | `session_key` | 64 random bytes | |
| `RATE` | `<ip-or-/64>#<minute>` | `n`, `ttl` | |
| `UPLOAD` | `<id>` | `kind`, `max_bytes`, `ttl` | |

- **The board** is one `Query(pk=TREE, begins_with(sk, "run#"))` and one project read. Jobs listing uses `status-index` for `queued` and `running`, plus recent `done` and `failed`.

**ADR-5: the mirror, manifest-based, with deletes and fencing.**
- **A tree** is a key prefix in S3 (`runs/<id>/`, `runs/.batches/<id>/`, `project/tasks/<task>/`, `files/tools.json`) plus its `TREE` item: a `version` and a **manifest** listing every key with its etag.
- **`pull(tree)`:**
  1. Read the `TREE` item (its version and manifest) **first**.
  2. GET the keys whose etag differs from this instance's copy, 16 threads in parallel.
  3. **Delete local files that aren't in the manifest.** This propagates deletes (pass 1 #9).
  4. Record the version that was read.

  Pulling all runs is used by the dashboard, prompts, tools and the rubric overview (`versions()` globs every `scores.json`, pass 1 #9).
- **`push(tree, changed, deleted, holder)`:**
  1. PUT the changed files to **unique keys**: `<key>@v<version+1>.<16-hex nonce>`, with `If-None-Match: *`, so no two writers can ever share or overwrite a key (pass 2 #1).
  2. Commit with one `TransactWriteItems`, with a **`ClientRequestToken`** (the push's nonce), so botocore's retry of an ambiguous attempt is idempotent for 10 minutes (pass 3 #3):
     - an `Update` of the `TREE` item: version+1 and the new manifest, condition `version = :expected`
     - for leased trees, a `ConditionCheck` that the `LOCK` lease is still held by this writer (`owner = :me AND expires > :now`). A condition can't see another item, so the lease check needs its own `ConditionCheck` (pass 2 minor).
  3. **The manifest maps each logical path** to `{key: <physical key @v<n>.<nonce>>, version_id: <S3 VersionId from the PUT>, size, etag}`.
     - **Readers GET by `VersionId`** (`s3:GetObjectVersion`), so a version they read from a manifest stays readable for the 35-day noncurrent window, even after the pusher deletes it (pass 3 #2).
     - On any 404, the reader re-reads the `TREE` item once and retries.
  4. **After a commit,** the pusher DELETEs the keys its manifest superseded, which makes them noncurrent versions.
     - **After a cancelled commit,** the writer re-reads the `TREE` item first. If the committed manifest carries its own nonce (an earlier attempt applied), it treats the push as committed. Otherwise it DELETEs its own new keys (pass 3 #3).
  5. With versioning on, the DELETEs turn the objects into noncurrent versions, which a `NoncurrentVersionExpiration` rule removes after **35 days**, the O4 restore window. That matches the tables' PITR, so a restore can use any manifest from the last 35 days (pass 2 #2).
  6. Readers only ever GET manifest-referenced object versions, so no lost update or delete race can show (pass 1 #11, pass 3 #2).
  7. **Single `PutObject` per file,** never `upload_file`, which goes multipart above 8 MB. Lifecycle also has `AbortIncompleteMultipartUpload` (1 day) and `ExpiredObjectDeleteMarker`.
  8. Orphan current objects from a crashed push (written, never committed, never deleted) are left in place, which is accepted (tiny). `cloud.sh gc` lists and deletes keys under a tree that no manifest references.
  5. Inside a run tree, `scores.json` and `report.md` are written last within the same manifest version, so they're atomic for readers anyway.
- **Who writes which tree:**

| Tree | Written by | Concurrency control |
|---|---|---|
| `run#<id>` | its run worker (sync), its evaluation worker | one run worker per run (job claim). An evaluation holds `LOCK eval#<id>` for the **whole** job, push included (pass 1 #8). A run being evaluated is no longer being run. |
| `batch#<id>` | Prepare, each item's row step, Finalize | `lease#batch#<id>` around each read-modify-push of `summary.csv` |
| `project#<task>` | API store operations, draft workers | `lease#project#<task>`: 120 s lease against a 60 s API timeout and an 8 s store operation, fenced by `holder` at push |
| `file#tools` | tools worker | single writer |

- **S2 (lost updates between browser edits):** `PromptBody`, `StagesBody`, `DraftBody`, `ArchiveBody` and `HistoryBody` gain `base_version`, the project tree version the page loaded (returned in `PromptDetail`, `PromptsResponse`, `DraftView` and `RubricOverview`).
  - Under the lease, the store operation refuses with 409 "Changed by someone else; reload" when `base_version` isn't current. A missing `base_version` (an old client) is refused only in cloud mode.
  - **Locally,** the version is the project directory's own counter, so the contract is one shape.
- **The sync thread in run workers:**
  - It skips `.`-prefixed files (the engine's `_atomic_write` temporaries and `.eval-*`), and tolerates files vanishing mid-scan.
  - It pushes every 5 s, and at exit.
  - Each push also recomputes the run's `card` and `summary` with `services.runs` card code on the local directory.

**ADR-6: jobs on DynamoDB and Lambda.**
- **Start**, one `TransactWriteItems` (pass 1 #2, #10):
  - a `JOB` put with `status=queued`, `reservation` and `month`
  - `SPEND` reserve (ADR-8)
  - for UI runs and batches, a **`SLOT` put** conditioned on `attribute_not_exists(pk) OR expires < :now`
  - for scorings, the `LOCK eval#<id>` put

  Then an async `Invoke` of the worker. If the invoke call itself fails, the API runs the exit transaction with `actual=0` and returns 500.
- **Worker configuration:**
  - async `MaximumRetryAttempts=0` and `MaximumEventAgeInSeconds=900`, so a throttled event can't start hours later (pass 1 small; invocation-async-configuring.html)
  - the worker's first act is a conditional `queued → running` update, so a duplicate delivery exits
- **Exit:** a transaction conditioned on `status = running`:
  - status `done` or `failed`
  - `SPEND` adjustment (ADR-8)
  - release the `SLOT` and `LOCK`

  That books the cost **exactly once** (pass 1 question 4).
- **Deadlines and the sweep:**
  - Non-batch jobs carry `deadline = started_at + 900 s + 120 s`; queued jobs carry `queued_at + 900 s` (their event age).
  - The **sweep** runs at the start of the jobs and board reads, at most once a minute per instance. It sends the same exit transaction, conditioned on `status IN (queued, running) AND deadline < :now`, with `status=interrupted` and `actual = reservation`.
  - **Batches have no deadline** (pass 1 #4). Their sweep calls `DescribeExecution` on `execution_arn`, and finalizes only when the execution has ended without its Finalize having run.
- **Interrupted runs:** the card shows `interrupted`. Scoring is allowed when the run tree has a `trace.jsonl` and no live run job. The worker then passes `--force`.
- **Pushes by kind:**

| Kind | Worker does | Pushes |
|---|---|---|
| run | bootstrap (ADR-11); `foundry run --runs-dir /tmp/state/runs …` with the sync thread | `run#<id>` |
| evaluate | bootstrap; pull `run#<id>`; `foundry evaluate <dir> --config …` (no `--lock-held`: the engine takes its own `.evaluating` in a private `/tmp`; the cross-instance guard is the DynamoDB lock the worker holds until after its push) | `run#<id>` |
| draft | bootstrap; pull the upload; the drafting call; then, under the project lease, the store's draft write | `project#<task>` |
| tools | `foundry tools --out /tmp/state/files/tools.json` | `file#tools` |
| import (pass 1 small: no 50 MB parse in the API) | pull the upload; `import_spans` | the new `run#<id>` trees |
| batch item | ADR-7 | |

- **Backend-only state reads (pass 1 #10):**
  - `prompts.pending(pid)` queries live `JOB` items carrying `pid` in `prompt_ids`.
  - `set_text` runs under the project lease, and run start reads the prompt under the same lease, so "no run may start between the check and the write" holds across instances.
  - `batches.batch_view`, `tools.tool_menu` and job lookups go through `jobs.*`, never `ws.jobs`.
- **Logs:** each worker writes `jobs/<id>.log` at exit and every 30 s, and `log_text` reads it.

**ADR-7: batches on Step Functions** (pass 1 #4–#6).
- **`Prepare`** (worker kind `batch_prepare`):
  - `engine.batch` expansion, with the derived configs written to `runs/.batches/<id>/configs/*.toml`
  - writes `batch.json` and an empty `summary.csv`
  - pushes `batch#<id>`
  - returns items `{n, variant, prompt, repeat, config_key}`
- **`Map`:**
  - inline, `MaxConcurrencyPath: $.concurrency` (state-map.html)
  - **each item's `LambdaInvoke` has `retryOnServiceExceptions=false` and no Retry** (pass 1 #6).
  - **Every item task has a `Catch` (`States.ALL`) leading to a `Pass` state** that returns `{n, status: "failed", error}`. So no item failure, including a worker timeout, OOM or throttle, ever fails the inline Map (pass 3 #1).
    - `ToleratedFailurePercentage` exists only for Distributed Map (state-map-inline.html).
    - The only `Retry` is on `Lambda.TooManyRequestsException`: 3 attempts, backoff 2, because a throttled invocation never ran.
    - One failed item doesn't stop the batch, as in `engine/batch.py` (proposed answer to the reviewer's question 1).
  - Each item is one worker invocation of kind `batch_item`:
    1. **Claim** a `JOB` for the item (`parent=batch_id`, conditional put on a deterministic `job_id = <batch>#<n>`). A Step Functions redelivery finds it claimed, and returns its recorded result.
    2. **Check the budget:** read the batch `JOB`'s `spent`. At or over `max_cost`, the item is `skipped (budget)`, the same rule as `engine/batch.py`.
    3. **Run:** pull the derived config from `config_key`, bootstrap, `foundry run`, and push `run#<id>`.
    4. **Evaluate,** if set, in the same invocation (both fit in 900 s: run ≤ 300 s, evaluation ≈ 120 s).
    5. **Book** the item's actual cost in a transaction: `ADD spent :c` on the batch `JOB`, conditioned on its `status = running`. If the batch has already been finalized, the cost goes straight to `ADD committed :c` on the batch's start-month `SPEND` item instead, so no late cost escapes the cap (pass 2 #3).
    6. **An item whose worker timed out or crashed** is swept like any job (its `<batch>#<n>` `JOB` has a deadline), and charged `run_wc + eval_wc` through the same rule.
    7. **Rewrite `summary.csv`** under `lease#batch#<id>`, and push `batch#<id>`, so a live batch shows its rows (pass 1 #5).
       - This is new code: read the CSV, replace row `n` with `summary_row(job)`, write it back. `engine.batch.write_summary` needs every `Job`.
- **`Finalize`** (also the target of a `Catch` on Prepare and on Map-level errors, with the error recorded): closes the batch.
  - On the normal path it runs after every item has returned (each item's task-level Catch guarantees that), so no sibling is still spending.
  - On the `Catch` path (Prepare failed, or a Map-level error), late item costs are still booked by the item rule (`status = running` else straight to `SPEND`).
  - The batch `JOB` is created with `spent = 0` and moves to `running` in Prepare. Finalize accepts `status IN (queued, running)`.
  - `SPEND`: `committed += spent − reservation` on the **start month's** item, in a transaction that also sets the batch `JOB` to `done`, conditioned on `status = running AND spent = :spent_read`. On a conflict it re-reads and retries (at most 5 times).
  - Status `done` or `failed`, and the slot released.
  - **A failed execution always closes its reservation** (pass 1 #4).

**ADR-8: spend on DynamoDB, with conditions DynamoDB can evaluate** (pass 1 #2, #3).
- **One attribute per month item:** `SPEND#<YYYY-MM>.committed` = what was spent plus what's still reserved.
- **Reserve:** `ADD committed :r`, condition `attribute_not_exists(committed) OR committed <= :limit`, with `:limit = cap − r` computed by the app. When `r > cap`, the start is refused before the call. A ConditionExpression can't do arithmetic, so the app supplies the threshold.
- **Settle:** at exit, on the **start month's** item (`JOB.month`): `ADD committed :(actual − r)`.
  - A job starting on October 31 and ending on November 1 books against October, its reservation month. That's stated, and it's the month the cap allowed it in.
  - The ledger's "finished month" from spec A5 becomes "**started** month" in the spec, so the two agree.
- **Reservations** are computed from config limits and prices, with a +10% margin.
- **Stated gaps:**
  - one call's input overshoot past a projection
  - retries billed after a client timeout aren't recorded
  - a batch child's failed evaluation that spent money records nothing
- The spec's A5 says "started in", which matches.

**ADR-9: auth, and stateless CSRF** (pass 1 #1).
- **Tokens:** `fdy_<32 random bytes, base64url>`, names `[a-z0-9-]{1,32}`, stored as sha256 in `TOKEN` items, cached 60 s.
- **Session cookie:** `<name>.<expiry>.<HMAC(session_key, name|expiry|token_sha256)>`, with `HttpOnly; Secure; SameSite=Strict; Max-Age=43200`.
- **CSRF without process state:**
  - **For cookie sessions:** the token is `HMAC(session_key, "csrf" | session_cookie_value)`. `/api/session` returns it, and any instance can verify it.
  - **Header-token (`X-Foundry-Token`) requests are exempt,** because no browser sends that header ambiently.
  - **Locally** (auth off), the per-process token stays as today.
- **Endpoints:** login (CSRF-exempt, Origin-checked, JSON only, rate-limited), logout, me.
- **Guard order:**
  1. `/healthz`
  2. Host = `requestContext.domainName`, or loopback locally. A harmless no-op in the cloud, since function URLs route by Host; Origin does the work.
  3. **Unauthenticated paths:** `/`, the SPA routes, `/assets/*`, `/cli/foundry-remote.pyz`, `POST /api/auth/login`. They hold no data.
  4. Auth on all other `/api/*`: 401, or 500 when DynamoDB is unavailable (fail closed).
     - A request with `X-Foundry-Token` is judged by that header **only**, never falling back to the cookie.
  5. Origin and CSRF on cookie-session writes.
- **Logout** clears the cookie in the browser. A copied session cookie stays valid until it expires (12 h) or its token is revoked, since sessions are stateless. Revocation is the real kill switch, and that's stated.
- **The session key** is created once by `cloud.sh deploy`, with a conditional put (`attribute_not_exists`), so two cold instances can never mint different keys.
- **Rate limit:** `RATE#<ip>#<minute>`. IPv6 is keyed by its /64 (kept from the Fargate gate). The limit is 10 a minute on sign-in attempts and wrong tokens; TTL 2 minutes.
- **Never logged:** headers, cookies or tokens.

**ADR-10: uploads with presigned POST** (pass 1 #13).
- **`POST /api/uploads {kind, filename, size}`** puts an `UPLOAD` item and returns a **presigned POST**: `url` plus `fields`, with a `content-length-range` condition at the kind's maximum, valid for 10 minutes.
  - **The client is pinned to SigV4** and the regional virtual-hosted endpoint: `Config(signature_version="s3v4", s3={"addressing_style": "virtual"}, region_name="us-west-2")`.
  - The default `boto3` 1.43.122 client signs SigV2 against the global host (probe, pass 2 #4), which buckets created after June 2020 reject, and which the CSP wouldn't allow.
  - `tests/test_uploads.py` asserts the URL host and the `x-amz-*` fields.
- **The CSP** `connect-src` adds `https://<state-bucket>.s3.us-west-2.amazonaws.com`. The API knows the bucket name from its environment and sets the header per response.
- **Bucket CORS:** `AllowedOrigins: ["*"]`, methods POST only, no credentials. Safe, because only a valid presigned policy can write, and only one key. This keeps the data stack free of any reference to the app's URL (D3).
- **Imports and drafts** take `upload_id`, check the object's size, and run as worker jobs (ADR-6).
- **Locally,** `POST /api/uploads` returns a same-origin URL (`/api/uploads/<id>`) that accepts the same POST form, so the client code is one path.

**ADR-11: the image and the worker bootstrap** (pass 1 #7).
- **One ARM64 image** (`public.ecr.aws/lambda/python:3.12`) for every function, with different handlers:
  - code, `config/`, `fixtures/`, default `tasks/` and `web_dist` under `/app`
  - RODA installed at build time (`uv tool install awslabs.roda-mcp-server==0.1.7` into `/opt/uv-tools`)
  - the cloud config overlay (`FOUNDRY_MCP_RODA_CMD`) pointing at it
- **Bootstrap** (`foundry/cloud_boot.py`, shared by the api and worker functions), once per cold start:
  1. `/tmp/state/project/config` → `/app/config`, and `fixtures` the same (symlinks).
  2. Pull `project#<task>` for every task, which gives `tasks/<task>/prompts.jsonl`, `expected.jsonl`, `rubric.json`, `system.txt`, history and drafts. Workers pull again at job start (a cheap version check).
  3. **First deploy:** if `project#<task>` has no `TREE` item, upload `/app/tasks/<task>` as version 1. So a fresh deployment has prompts (pass 1 question 2).
  4. Point `HOME`, `UV_CACHE_DIR` and `TMPDIR` under `/tmp`.
- **`FOUNDRY_S3_URI` is unset in the cloud** (pass 1 question 1). The engine's own `storage.mirror` doesn't run, and the manifest push is the only writer of `runs/<id>/`. `FoundryStack`'s runs bucket isn't part of a cloud deploy.
- **RODA's credentials** (pass 1 question 3): spike S-2 showed RODA's tools work **without** AWS credentials. `mcp.server_env` strips `AWS_*` and Lambda has no `~/.aws`, yet a live run made 10 tool calls with 0 errors. So there's no `pass_aws_env` flag.

**ADR-12: stacks and limits.**

| Stack | Holds |
|---|---|
| `FoundryDataStack` | the state bucket (versioned, SSE-S3, block-public, SSL-only, POST-only CORS, lifecycle: `uploads/` current objects expire in 1 day, noncurrent versions expire in 35 days), the table (PITR, the GSI), the SNS topic with its subscription. All `RETAIN`. No references to the app stack. |
| `FoundryAppStack` | the functions (one image asset), the api function URL (`AuthType NONE` plus `lambda:InvokeFunctionUrl` **and** `lambda:InvokeFunction` permissions for `*`, as required since October 2025, urls-auth.html), the batch state machine, alarms, log groups (30 days) |

- **No reserved concurrency by default** (pass 1 #14). Lambda keeps at least 100 unreserved (configuration-concurrency.html), and new accounts can have a quota of 10.
- **`cloud.sh deploy` preflights** `get-account-settings`:
  - **If unreserved ≥ 100 + 28:** reserve api 20 and worker 8.
  - **Otherwise:** none, with a printed note on requesting a quota increase.
- **Cost guard** (pass 1 question 5): an **invocation alarm**, api invocations > 5,000 in 15 minutes, emails the topic.
- **The kill switch:** `cloud.sh stop` sets the api function's reserved concurrency to 0, which is always allowed, and disables the probe.
- **Roles:**
  - **api role:** table, bucket read/write/presign, `lambda:InvokeFunction` on the worker, `states:StartExecution`/`DescribeExecution`/`StopExecution` on the state machine
  - **worker role:** Bedrock (the existing `bedrock_models()` statements), the bucket (`GetObject`, `GetObjectVersion`, `PutObject`, `DeleteObject`), and the table restricted with `dynamodb:LeadingKeys` to `TREE`, `JOB`, `LOCK`, `SLOT`, `SPEND` and `UPLOAD`. Workers read `UPLOAD` items for imports and drafts, and never query the GSI. Never `TOKEN`, `CONFIG` or `RATE`, so a worker can't read the token hashes or the session key (pass 2 minor). MCP servers get no AWS credentials at all (`server_env` strips them; spike S-2).
  - **state machine role:** invoke the worker
  - **probe role:** `PutMetricData` only

## App changes, by layer (the rules in `tests/test_architecture.py` hold)

| Layer | Change |
|---|---|
| kernel | `config.load_config`: `FOUNDRY_MCP_<NAME>_CMD` command override and `FOUNDRY_S3_URI` (built in slice 0) |
| data | `store.py` (S3, DynamoDB, Lambda and Step Functions clients, with conditional and transaction helpers), `mirror.py` (manifests, pull, push, fencing), `index.py` (cards and summaries), `tokens.py`, `spend.py` (local JSONL and DynamoDB), `locks.py` (file and DynamoDB backends, selected by `Workspace`; the engine's subprocesses always use files in their private `/tmp`), `uploads.py`, `batches.py` (reads a mirrored `runs/.batches/<id>`) |
| engine | **no change** |
| services | `jobs.py`: `Backend` interface, `LocalBackend` (today), `CloudBackend` (ADR-6), plus `jobs.live(kind=…, prompt_id=…)`, so nothing reads `ws.jobs` directly. `runs.board`: index-based in the cloud (cards from `TREE` items; KPIs and shares computed from `summary` plus the current active rubric's sha, so an activation updates them at once, pass 1 #12). `runs.cards`: the evaluating headline from `jobs.live(kind="evaluate")`. `prompts` and `rubrics`: lease, `base_version` checks, drafts as jobs. `batches.batch_view` and `tools.tool_menu` through `jobs`. New: `access.py`, `spend.py`. |
| api | `auth.py` (stateless CSRF); guard (domainName host, per-response CSP); `/healthz`; `/api/uploads`; the import, drafts and `base_version` body changes; `/cli/foundry-remote.pyz`; schemas and `types.ts` regenerated |
| entry | `lambda_api.py` (adapter), `lambda_worker.py` (dispatch only: one function per kind in `services/workers.py`, so the logic stays in services, pass 1 layer note), `lambda_probe.py`, `cloud_boot.py`, `cli_remote.py` (**standard library only**, packed by `scripts/build_cli.py` into `web_dist/cli/foundry-remote.pyz` with `zipapp`; spec I3). `ENTRY` gains these. |
| web | sign-in, 401 handling, "Signed in as", the presigned-POST upload flow, drafts polling, `base_version` in edits plus the 409 reload message, the interrupted note, the receiver hidden when off |
| infra | the two stacks; `tests/test_infra.py` (no `AWS::EC2::*` or `AWS::EFS::*` (X1); both function URL permissions; worker retries 0 and event age 900; Map `LambdaInvoke` with no retries; RETAIN; no data → app references) |
| scripts | `cloud.sh` (deploy with preflight and D5 timing, token, seed, logs, stop/start, destroy), `build_cli.py`, `docs/deploy.md`, `cloud_smoke.sh` |

## Tests

| Area | What it checks |
|---|---|
| Local backends | everything existing keeps passing (I4), with the two contract changes (I6) updated |
| Cloud backends against a `FakeStore` | a `FakeStore` implementing `store.py` with DynamoDB's condition and transaction semantics and S3 manifests, run through the real services. It checks: manifest pull deletes; the losing push leaves the winner intact; `base_version` 409; lease expiry and fencing; SLOT exclusivity across two "instances" (two Workspaces on one FakeStore); spend reserve and settle across a month boundary; duplicate delivery exits; the sweep books exactly once; batch items booking only the batch's `spent`; Finalize on Catch; stateless CSRF verified by another instance |
| Adapter | payload 2.0 fixtures: cookies, base64, multi-value headers, `sourceIp`, `domainName` |
| Auth | the 401 sweep, rate limit (including IPv6 /64), cookie binding, fail closed |
| Contract | the new routes and fields; `types.ts` |
| Infra | synth assertions |
| CLI | the `.pyz` built and run against a real local server: login, runs, run start `--wait`, batch, import, `--json`, exit codes |
| Live (Verify) | `cloud_smoke.sh`: sign-in, live run updates under 10 s (L1), scoring, a 2-item batch with live rows, an import, a draft, an interrupted run, two concurrent edits → 409, the restore drill, D5 timing |
| Pentest (scoped with the user) | unauthenticated surface, brute force, cookie and CSRF forgery, presigned scope, concurrent spend bypass, DNS rebinding on the rubric URL fetcher, subprocesses inheriting credentials |

## Cost (us-west-2, Price List API, 2026-10-31)

| Item | Idle month | About 500 runs |
|---|---|---|
| Lambda ARM (free tier 400k GB-s / 1M requests): runs 500 × 300 s × 2 GB, evaluations, API | $0 | ≈ $5.5 |
| Step Functions Standard | $0 | ≈ $0.1 |
| DynamoDB on-demand, PITR (about $0.20/GB-month on tiny data) | ≈ $0 | ≈ $0.2 |
| S3 storage, versions, requests | < $0.1 | ≈ $0.4 |
| ECR image (about 1.5 GB), CloudWatch logs, probe, alarms (3) | ≈ $1–1.5 (ECR and Logs prices unverified) | ≈ $2 |
| **Total before Bedrock** | **≈ $1–2** (C1 ≤ $3) | **≈ $8** (C1 ≤ $10) |

## Spikes (deploy to the sandbox account; before Build depends on them)

| Spike | Proves | Fallback |
|---|---|---|
| S-1 | image function plus function URL plus the adapter: cookies, `sourceIp`, `domainName`; a 55 s request | API Gateway HTTP API |
| S-2 | a worker runs a live Bedrock run: RODA from `/opt/uv-tools` (anonymous, or with `pass_aws_env`), STAC, stdio subprocesses, read-only filesystem, `/tmp` home, 13 tools, the sync pushing | `UV_OFFLINE` cache; more memory |
| S-3 | the Map with `MaxConcurrencyPath`, no item retries, Catch → Finalize | none needed |
| S-4 | 77 real runs: board from the index p50 < 300 ms; dashboard cold < 3 s, warm < 500 ms | precomputed dashboard aggregates |
| S-5 | presigned POST with `content-length-range` from the browser (CORS, CSP); `TransactWriteItems` with the `SPEND` and `SLOT` conditions | the API streams uploads in 5 MB parts |

## AC → component map

| AC | Component |
|---|---|
| X1 | infra synth test |
| X2 | per-use services |
| D1 | `cloud.sh` with preflight |
| D2 | environment from the stack; host from `domainName` |
| D3 | synth stability; POST-only `*` CORS, so no cross-stack references |
| D4 | data stack RETAIN |
| D5 | `cloud.sh` timing |
| I1–I2 | adapter, auth |
| I3 | `.pyz` from `/cli/` |
| I4 | local backends |
| I5 | ADR-11 |
| I6 | ADR-10, drafts and imports as jobs, auth, cap and receiver flags |
| I7 | `--no-receiver` |
| A1–A2 | ADR-9 |
| A3 | function URL only; presigned POST scope |
| A4 | roles, gitleaks |
| A5 | ADR-8 |
| A6 | ADR-9 rate limit |
| S1 | S3 and DynamoDB |
| S2 | ADR-5 (`base_version`, lease, fencing, new-key pushes) |
| S3 | deadlines, sweep, lease expiry |
| S4 | `cloud.sh seed`, refusing when any `TREE run#` exists |
| S5 | ADR-6 (in-flight invocations finish; the sweep marks interrupted) |
| L1 | sync thread every 5 s, plus polling |
| O1 | log groups, job logs |
| O2 | alarms, probe, invocation alarm |
| O3 | the CLI's batch commands |
| O4 | bucket versioning, PITR, restore drill |
| C1 | cost table |

## One-way doors

- **Deleting the state bucket or the table, or their versions and PITR.** Nothing does it; everything is `RETAIN`.
- **The public URL** (the user's decision).
- **The key design** (`TREE`, `JOB`, `SPEND`): reviewed here because it's costly to change once data exists.

## Gate record

**Design gate pass 1 (2026-10-31): 15 blockers, all resolved above:**

| # | Blocker | Fix |
|---|---|---|
| 1 | per-process CSRF | stateless HMAC CSRF; header tokens exempt |
| 2 | condition arithmetic | one `committed` attribute, app-computed threshold |
| 3 | ledger double-booking and month crossing | items book only the batch's `spent`; settle on the start month |
| 4 | batch deadline and failure | no deadline; `DescribeExecution` sweep; Catch → Finalize |
| 5 | batch dirs and configs not mirrored | the `batch#` tree with configs; `summary.csv` per item under a lease |
| 6 | Map retries re-running paid work | no retries; deterministic item job claim |
| 7 | workers without project files | `cloud_boot`: symlinks, project pull, first-deploy seed |
| 8 | evaluate lock | DynamoDB lock held through the push; no `--lock-held` |
| 9 | uncovered paths and deletes | tools tree; pull all for the rubric overview; manifests propagate deletes |
| 10 | `ws.jobs` and `ws.lock` invariants | `jobs.live`; `SLOT` items in the start transaction; the project lease for prompt edits vs run start |
| 11 | S2 | `base_version`, a no-TTL version, new-key pushes plus fencing |
| 12 | board from the index | index-based board, KPIs from summaries plus the active sha |
| 13 | uploads | presigned POST with a length range, per-response CSP, POST-only `*` CORS |
| 14 | reserved concurrency | none by default; preflight; invocation alarm; stop = reserve 0 |
| 15 | self-permission cycle | host from `requestContext.domainName` |

**Questions 1–5 answered** in ADR-11 (`FOUNDRY_S3_URI`, first-deploy tasks, RODA credentials), ADR-6 (exactly-once booking) and ADR-12 (invocation alarm).

**Smaller items folded in:** the IPv6 /64 key; stated spend gaps; pentest items; reading the index before the GETs; sync skip rules; lease margin; queued-job deadline and event age; `MaxConcurrencyPath`; imports as worker jobs; both function URL permissions; `lambda_worker` dispatching to `services/workers.py`.

**Design gate pass 2 (2026-10-31): four blockers, resolved above.**

| # | Blocker | Fix |
|---|---|---|
| 1 | shared new keys | unique `@v<n>.<nonce>` keys with `If-None-Match: *` |
| 2 | lifecycle can't see "unreferenced" | explicit DELETEs after commit or loss; noncurrent expiry at 35 days (O4) |
| 3 | batch spend leaks | items catch their own errors with `ToleratedFailurePercentage` 100; late item costs go straight to `SPEND`; crashed items are charged their worst case; Finalize settles conditionally on the `spent` it read |
| 4 | presigned POST signing | pinned SigV4 regional virtual-host, asserted by a test |

**Answers to the reviewer's questions (proposed by the builder; confirmed at sign-off):**
- A failed item doesn't stop its batch (as `engine/batch.py`).
- The restore window is 35 days.

**Pass 2 minors, folded into Build:**
- **Transactions and releases:**
  - Release `SLOT` and `LOCK` with a separate conditional delete after the exit transaction, never inside it (a mismatch would cancel the booking).
  - The invoke-failure path settles with the condition `status IN (queued, running)`.
  - `TransactionConflict` gets bounded retries, at most 5 with jitter, before a 409.
- **Consistency of live checks:**
  - Live-job checks under the project lease (`prompts.pending`, `set_text`) read the `SLOT` items and the batch `JOB`'s `prompt_ids` with strongly consistent `GetItem`, never the eventually consistent GSI.
  - Queued jobs carry a GSI sort value (`queued_at`).
- **Batches:**
  - A batch's `SLOT` has no expiry. It's released by Finalize or by the `DescribeExecution` sweep.
  - Item jobs (`<batch>#<n>`) follow the item booking rule, never charging `SPEND` directly except as stated.
- **Kernel and layers:**
  - The kernel overlay gains `FOUNDRY_S3_URI=""`, which overrides `[storage] s3_uri`, so the engine's `storage.mirror` is off in the cloud. Today's code has no such override; it's new (D1).
  - `services/workers.py` must not touch files or processes directly. The sync thread and the subprocess call live in `services/jobs.py` and `data/mirror.py` (the `FILE_ALLOWED` rule stands).
- **Reads the inventory missed:**
  - `runs.detail` reads "evaluating" from `jobs.live(kind="evaluate")`, not `lock.is_locked`.
  - `tools.tool_menu` and `refresh_tools` read `files/tools.json` through the mirror.
  - `prompts_view` and `prompt_view` read the index summaries instead of every run.
  - The worker builds `summary` with `runs.entries()` semantics: no scores when `evaluation_interrupted`, and spend from `stats.costs`. So the board and the dashboard agree.
- **Small fixes:**
  - The API overlays a swept `JOB`'s `interrupted` status on the card (`run.json` still says `running`).
  - `build_cli.py` writes the `.pyz` with fixed `ZipInfo.date_time`, so builds are reproducible (D3).
- **Doc fixes:**
  - The `MaxConcurrencyPath` source is state-map-inline.html (JSONPath only, not with `MaxConcurrency`; CDK 2.275 `max_concurrency_path`).
  - The spec's I6 says POST.
- **Accepted, and stated:**
  - A project-wide `base_version` can give a harmless spurious 409 when a draft lands during a prompt edit.
  - A queued job swept unrun is charged its reservation.

**Design gate pass 3 (2026-10-31): three blockers, escalated to the user with these proposed resolutions, applied above.**

| # | Blocker | Proposed fix |
|---|---|---|
| 1 | `ToleratedFailurePercentage` isn't available on inline Map | a task-level `Catch` (`States.ALL`) → `Pass {status: failed}` per item; `Retry` only on throttles; Finalize accepts `queued` |
| 2 | deletes racing pulls | the manifest stores `VersionId`; reads GET by version; one re-read on 404 |
| 3 | an ambiguous commit deleting its own keys | `ClientRequestToken` on the commit; a nonce check before deleting after a cancel |

**Pass 3 minors, folded into Build:**
- **Doc fixes:** the status line; the step numbering; the manifest shape in ADR-4.
- **Batches:**
  - `spent=0` at batch creation.
  - A Step Functions redelivery that finds its item job `running` waits, polling every 10 s up to the item deadline, then returns its result.
- **Writes:**
  - Single `PutObject`, plus the multipart-abort and delete-marker lifecycle rules.
  - The first-deploy seed creates the `TREE` item with `attribute_not_exists`.
- **API surface:**
  - The board's `Query` has a `ProjectionExpression` without `manifest`.
  - A 409 after the `TransactionConflict` retries run out joins spec I6's list.
- **Recovery:** `docs/deploy.md` describes the restore drill: restore the table to a point in time into a new table, read the `TREE` item, restore the object versions it names, and write it back.
- **Accepted:**
  - Orphan current objects from crashed pushes, with `cloud.sh gc`.
  - Presigned POSTs bounded by the signing role's session.

## Implementation plan

**How to read it:** each slice names the design sections that specify it. The mechanisms are defined there, not here:
- ADR-4: key schema
- ADR-5: mirror protocol (manifest by `VersionId`, unique-key push, `ClientRequestToken` commit, delete after commit, nonce check, lease `ConditionCheck`; `base_version`)
- ADR-6: jobs, `SLOT`, `LOCK`, sweep, sync thread, worker kinds
- ADR-7: batches
- ADR-8: spend
- ADR-9: auth (tokens, the cookie format, stateless CSRF, the rate-limit item, guard order and public paths)
- ADR-10: uploads
- ADR-11: image and bootstrap
- ADR-12: stacks and limits

Each slice ends green (full suite, Vitest, ruff, `tsc`, gitleaks) and is its own commit. Everything is pushed after Verify.

**Where code goes** (the layer rules in `tests/test_architecture.py`):
- The Lambda entry modules (`lambda_api.py`, `lambda_worker.py`, `lambda_probe.py`, `cloud_boot.py`, `cli_remote.py`) are top-level `src/foundry/*.py`, added to `ENTRY`.
- Backends live in `data/`, job and worker logic in `services/`, the HTTP surface in `api/`.
- **Infrastructure:**
  - New stacks live in `infra/foundry_cloud/`: `data_stack.py`, `app_stack.py`, `batch_sm.py` (the state-machine definition as a function returning the CDK chain, unit-testable by synth).
  - The existing `infra/stack.py` (`FoundryStack`: runs bucket and `HarnessRole`) is kept for the laptop's S3 mirror. Its `bedrock_models()` is reused by the worker role.
  - `infra/app.py` synthesizes all three stacks; `cloud.sh` deploys only the two new ones.

**Prices for reservations (A5):** the config's own `[prices]` table, input and output per million tokens, which every Bedrock config already has. A model missing from `[prices]` while a cap is set makes `services/spend.reservation()` raise a 409: "No price for <model> in <config>; add it to [prices]".

| # | Slice | Delivers (per the design section named) | Done when | Depends on |
|---|---|---|---|---|
| 0 | **Spikes:** `infra/spike/`, a throwaway `FoundrySpikeStack` (one image, an api function with URL, a worker function, a small state machine, a bucket, a table), deployed and then destroyed | S-1, S-2, S-3, S-5 (the spike table). **What each gates:** S-1's adapter and host trust → slice 1 (fallback: API Gateway HTTP API). S-2's RODA credentials and the offline start → slices 1 and 3 (`pass_aws_env` or not). S-3's Map shape → slice 5. S-5's presign config and transaction shapes → slices 2, 4 and 6. | each finding recorded in its ADR; the spike stack destroyed | sign-off |
| 1 | **Image and edge** | `Dockerfile.lambda`, `.dockerignore` (ADR-11); `lambda_api.py` adapter (ADR-3); `cloud_boot.py`; `/healthz`; the guard trusting `requestContext.domainName` as the host (ADR-3, ADR-9 step 2) | adapter tests with payload 2.0 fixtures; the image builds through `CDK_DOCKER` (Docker, Finch or Podman; this laptop has Podman, so `cloud.sh` accepts all three) | 0 |
| 2 | **State store and mirror** | `data/store.py` (clients and condition and transaction helpers, the presign config from S-5), `tests/fakestore.py`, `data/mirror.py` (ADR-5 in full), `data/index.py` (ADR-4 `TREE`) | FakeStore tests: manifest pull deletes, a losing push leaves the winner intact, nonce check after an ambiguous commit, lease fencing, reading by `VersionId` | 1 |
| 3 | **Jobs, locks, slots, sweep, workers** | `services/jobs.py` (`Backend`, `LocalBackend` = today, `CloudBackend`), `services/workers.py` (kinds run, evaluate, tools, import, draft), the sync thread (ADR-6), `lambda_worker.py`; `data/locks.py` backends; `jobs.live`; the readers switched to jobs and index (pending, detail evaluating, `batch_view`, `tool_menu`, the index board, prompt views); **I7:** `Workspace.receiver_enabled` plus the 409 in `services/imports.receiver_start` | two Workspaces on one FakeStore: slot exclusivity, a duplicate delivery exits, the sweep books once, interrupted overlay; local e2e unchanged | 2 |
| 4 | **Spend** | `data/spend.py` (JSONL and DynamoDB), `services/spend.py` (ADR-8: reservations from `[prices]`, threshold condition, start-month settle) | reserve and settle across a month boundary; refusal message; no cap locally | 3 |
| 5 | **Batches** | worker kinds `batch_prepare`, `batch_item`, `batch_finalize` (ADR-7); `infra/foundry_cloud/batch_sm.py` (per-item Catch, throttle-only Retry, `MaxConcurrencyPath`); the summary row rewrite | synth tests on the state-machine JSON; FakeStore item booking (running vs finalized routing); Finalize on Catch | 3, 4 |
| 6 | **Auth and API surface** | ADR-9 (`data/tokens.py`, `services/access.py`, `api/auth.py`; public paths `/`, `/assets/*`, `/cli/foundry-remote.pyz`, login); ADR-10 `/api/uploads` plus the local route; import and draft by `upload_id`; `base_version` bodies (ADR-5); async drafts; schemas plus `types.ts` | the 401 sweep, rate limit (IPv6 /64), cookie binding, stateless CSRF across two instances, fail closed, presign host and field assertions; contract tests | 2, 3 |
| 7 | **Web** | sign-in, 401 handling, "Signed in as"; POST upload flow; drafts polling; `base_version` with the 409 reload; interrupted note; receiver hidden when off | Vitest; local e2e (updated for I6 only) | 6 |
| 8 | **CLI** | `cli_remote.py` (standard library only), `scripts/build_cli.py` (zipapp, fixed timestamps) → `web_dist/cli/foundry-remote.pyz`; `cli.py` gains `foundry remote …`, delegating to `cli_remote.main` (I3's builder path) | the `.pyz` run against a real local server: login, runs, run start `--wait`, batch, import, `--json`, exit codes | 6 |
| 9 | **Infrastructure and operations** | `FoundryDataStack` (bucket: versioning, lifecycle, POST CORS; table: PITR 35 days, GSI, TTL; SNS; all RETAIN) and `FoundryAppStack` (functions with log retention 30 days, URL with both permissions, the state machine from `batch_sm.py`, alarms, the probe, concurrency settings per preflight); `tests/test_infra.py`; `scripts/cloud.sh`: preflight; `deploy --email <addr> [--budget <usd>] [--cap <usd>]` (CDK parameters: the SNS email subscription in the data stack; the optional account budget; the cap), always naming its two stacks (`FoundryDataStack`, `FoundryAppStack`) so the legacy `FoundryStack` is never touched; **O4 safe rollout**: the function URL targets alias `live`, and `cloud.sh` publishes the new version, invokes it with a synthetic `/healthz` and `/api/session` event, and only then moves `live` (a failed check leaves `live` on the previous version); D5 timing; writing the URL into the operator's `remote.json` (D4), the session key create, `token`, `seed` (refused when any `TREE run#` exists, S4), `gc`, `logs`, `stop`/`start`, `destroy`; `docs/deploy.md` (fresh account, restore drill, SCP note) | synth tests (X1, RETAIN, permissions, no data → app refs) | 1–8 |
| 10 | **Deploy and Verify** | deploy to the sandbox; seed the 77 runs; **D3**: a second `cloud.sh deploy` with no change shows an empty `cdk diff` and no new version; **O4**: deploy a version whose `/healthz` fails, and show `live` stays on the previous version and keeps serving; **S-4:** board p50 from the index under 300 ms, dashboard cold under 3 s and warm under 500 ms, measured with the real 77 runs; `cloud_smoke.sh` (sign-in, L1 under 10 s, scoring, a 2-item batch, an import, a draft, an interrupted run, two concurrent edits → 409, CLI from `curl`); restore drill; pricing (C1); the handoff. **The pentest runs only with the user's OK** on its scope, and isn't required for the other ACs. **The fresh-account success signal** is exercised at the hackathon account deploy: this slice runs the same `docs/deploy.md` steps in the sandbox and records the time. | every AC proven and recorded | 9 |

**Local parity rule:** after every slice, `foundry ui` on the laptop and the existing e2e scripts pass unchanged, apart from I6's two contract changes, which slices 6 and 7 bring with their tests (I4).

**Plan gate:** 3 passes (2026-10-31). Pass 3 found no blockers. Its notes are folded in here:
- **Slices 7 and 8:** the cap's 409 (spend, cap, reset date) shown in the UI banner and as CLI stderr.
- **Slice 9:**
  - Workers and the state machine invoke the worker's alias `live`, so an in-flight invocation finishes on its version.
  - `cloud.sh` publishes a new version only when the image digest changed (D3).
  - The pre-switch check calls `/healthz` and `/api/session`, an existing route.
- **Slice 10:**
  - a run in flight across a deploy (S5)
  - `cloud_smoke.sh` checks 13 tools and that the RODA command is `/opt/uv-tools/bin/...` (I5)
  - D5's time recorded in the handoff
  - X2 checked from CloudWatch: zero invocations and zero cost in an idle hour, apart from the probe

## Spike results (slice 0, 2026-10-31, the sandbox account, stack `FoundrySpikeStack`, since destroyed)

| Spike | Result | Evidence | Consequence |
|---|---|---|---|
| S-1 function URL + adapter | **pass**, with one adapter bug found and fixed | `/healthz` 5.5 s cold, 0.25 s warm; `/api/session` 200 through the real app; a foreign Origin gets 403; the domain's own `https://` origin passes the guard; the SPA HTML is 200. **JS assets came back as 0 bytes:** the adapter answered `http.disconnect` after the body, and Starlette's `FileResponse` listens for disconnects, so it aborted. Fixed (`receive` now waits, as a real server does); `test_streamed_file_responses_arrive_whole`. | ADR-3 holds. The 55 s request wasn't needed (drafting is async). CDK 2.275 adds **both** function-URL permissions (`InvokeFunctionUrl` and `InvokeFunction`) on its own. |
| S-2 worker | **pass** | An offline run in real Lambda: succeeded, 24 s cold, 2 tool calls through RODA (`/opt/uv-tools/bin`, via `FOUNDRY_MCP_RODA_CMD`) and STAC, on a read-only filesystem. **A live Bedrock run (`config/bedrock.toml`):** succeeded, 32 s, **10 tool calls, 0 tool errors**, $0.158. `foundry tools` in the image: 13 tools, 0 server errors. | RODA works **without** AWS credentials (`server_env` strips them), so `pass_aws_env` is **dropped** from the design. ADR-11 holds as built. |
| S-3 Step Functions Map | **pass**, with one fix | `MaxConcurrencyPath` = 2 respected (two waves); a deliberately failing item → `{status: failed, error}` via the task `Catch`; the execution SUCCEEDED. | The `Catch` must keep the item's input (`result_path: "$.error"`), so the failed item's `n` survives; the spike's Pass replaced it. Recorded for `batch_sm.py`. |
| S-5 transactions + upload | **pass** | **Spend reserve:** the threshold condition refuses past the cap (`ConditionalCheckFailed`). **Idempotency:** a retry with the same `ClientRequestToken` doesn't double the reservation (total 8.1, not 12.1). **SLOT:** a second taker is refused while the slot is held; an expired slot can be taken. **Browser presigned POST** (pinned SigV4, regional virtual host) under a CSP naming the bucket: 1 KB cap, 100 B → 204, 5 KB → 400 (S3 enforces `content-length-range`). | ADR-8, ADR-10 and ADR-6 hold. **The `protagona-sandbox-1` profile's default region is us-east-1**, so every boto3 client in `cloud.sh` and the scripts passes `region_name="us-west-2"` explicitly. |

**Container tooling:** Podman 6.1.4 (arm64) built and pushed the image through `CDK_DOCKER=podman`. The first deploy took 7 minutes, mostly the initial image push.
