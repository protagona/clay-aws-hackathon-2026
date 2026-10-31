# SUPERSEDED (2026-10-31): the user chose a serverless design (design.md). Kept for its gate record. — Fargate design

**Spec:** ./spec.md   **Date:** 2026-10-31   **Status:** Design gate pass 2 (pass 1's 13 blockers and 6 questions answered; see "Gate record")

## The shape

```
 browser ──────────────────┐
 foundry remote (any machine) ─┤  HTTPS  https://<id>.cloudfront.net   (no domain needed)
 curl / scripts ───────────┘
                     │
   CloudFront ── /assets/*: cached · everything else: CachingDisabled, origin policy AllViewerAndCloudFrontHeaders-2022-06
                │       503 from the origin → /paused.html (a tiny S3 origin with OAC)
                │ VPC origin, HTTP only, port 80 (no internet path to the ALB)
  ┌─────────────┼──── FoundryDataStack (kept by destroy) ────────────────────────────────────────────────┐
  │ VPC, 2 AZs, no NAT · EFS (Elastic throughput, encrypted) + AWS Backup daily/35 d · token secret       │
  │  ┌──────────┼──── FoundryAppStack (deploy/destroy freely) ────────────────────────────────────────┐ │
  │  │ private subnets: internal ALB :80 (SG ingress: CloudFront origin-facing prefix list)             │ │
  │  │ public subnets:  ECS service, 1 Fargate task, ARM64, 1 vCPU / 3 GB                              │ │
  │  │                  SG: 8765 only from the ALB SG · public IP for egress only                      │ │
  │  │   foundry ui --host 0.0.0.0 --public-url … --auth-secret … --monthly-cap-usd 30 --no-receiver  │ │
  │  │   /data ← EFS (runs/, project/tasks/)                                                           │ │
  │  │ health probe: Lambda every 5 min → https://<id>.cloudfront.net/healthz → metric → alarm → email │ │
  │  └─────────────────────────────────────────────────────────────────────────────────────────────────┘ │
  └────────────────────────────────────────────────────────────────────────────────────────────────────────┘
  FoundryStack (existing: runs bucket, access logs, HarnessRole) is unchanged.
  Egress from the task: Bedrock, S3 (runs bucket, open data), Earth Search, Secrets Manager. All us-west-2 or public.
```

## Decisions (ADRs)

**ADR-1: one Fargate task with EFS. Lift the app as it is.**

| | Option | Running cost/mo | Verdict |
|---|---|---|---|
| **A** | **ECS Fargate, 1 task + EFS** | **≈ $62** | **chosen:** runs today's single-process app unchanged, with managed deploys, logs and rollback |
| B | 1 EC2 instance + EBS | ≈ $35 | we'd own the patching, the supervisor, the backups and a hand-rolled deploy |
| C | App Runner | — | no persistent volume (source lookup #3) |
| D | Serverless (Lambda, S3, DynamoDB, Step Functions) | ≈ $5 idle | rewrites the data and job model, and batches outlive Lambda's 900 s (#11). That's Phase 2. |

**ADR-2: the front door is CloudFront, with a VPC origin to an internal ALB.**
- **HTTPS on `*.cloudfront.net`,** so no domain is needed.
- **The VPC origin** (`origins.VpcOrigin.with_application_load_balancer`, aws-cdk-lib 2.275.0):
  - `protocol_policy=HTTP_ONLY` and `http_port=80`. The default, `MATCH_VIEWER`, would try HTTPS against an HTTP-only ALB, and every request would 502 (design gate #1).
  - `read_timeout=60 s`, within the default 1–120 s quota (cloudfront-limits.html, checked by the gate).
  - `keepalive_timeout=5 s`.
- **The ALB is internal,** in private-isolated subnets, so it has no internet route.
  - **Its security group admits the CloudFront origin-facing managed prefix list** (`com.amazonaws.global.cloudfront.origin-facing`), the documented option 1 (private-content-vpc-origins.html).
  - The stricter option, `CloudFront-VPCOrigins-Service-SG`, exists only after the VPC origin is created, and needs a custom-resource lookup (gate #2). It's not worth it while the ALB has no internet route anyway.
  - **Spike S-5** checks that the prefix list fits the security group's rule quota.
- **Origin request policy:** `AllViewerAndCloudFrontHeaders-2022-06`. It forwards all viewer headers (Host, cookies, `X-Foundry-Token`) **and** CloudFront's own `CloudFront-Viewer-Address`, which the sign-in rate limit keys on (gate #5) (using-managed-origin-request-policies.html).
  - **Spike S-5** shows that a viewer-supplied `CloudFront-Viewer-Address` is overwritten, not passed through.
  - If it's passed through, the key becomes the second-from-right entry of `X-Forwarded-For`: the address CloudFront saw, appended by the ALB.
- **`/assets/*`:** `CachingOptimized`; the files are content-hashed.
- **Paused page (I6), design gate pass 2 #1:**
  - The ALB answers 503 when it has no healthy target. A CloudFront custom error response maps 502, 503 and 504 to `/paused.html`, with **`ResponseCode` pinned to 503** and a 10 s error-caching TTL.
  - **`/paused.html` has its own cache behaviour,** pointing at a private S3 bucket through Origin Access Control. The bucket lives in the **app** stack (pass 2 #2: its OAC policy names the distribution, so in the data stack it would form a cycle).
  - **The app never sends 502, 503 or 504 itself,** so those codes always mean "the origin is down", never an app answer:
    - the draft route's model failure changes from 502 to **424** "The model could not draft…"
    - the auth outage is **500** "Sign-in is unavailable; try again shortly"
    - **a test checks every `ServiceError` status and guard response** for 502–504
  - **The probe** reads `/healthz` and requires the body `ok`, so a paused (503) or dead app is never counted Up.
- **Timeouts and keep-alive (gate #4):**
  - ALB idle timeout 120 s.
  - `foundry ui` runs uvicorn with `timeout_keep_alive=130` whenever `--host` isn't loopback. The target's keep-alive must exceed the ALB's idle timeout, or the ALB reuses connections the target has already closed and returns 502s.

**ADR-3: no NAT gateway.**
- The task sits in a public subnet with a public IP for egress only. Its security group admits 8765 from the ALB security group, and nothing else.
- **Savings:** a NAT gateway is about $33 a month; the task's public IPv4 costs $3.65 (#6).

**ADR-4: one writer. Every job runs inside the app's task.**
- **Single instance:** desired count 1, `minimumHealthyPercent=0`, `maximumPercent=100`, so the old task stops before the new one starts (deployment-type-ecs.html). A circuit breaker rolls back a failed deploy.
- **Jobs:** remote CLI jobs go through the API, which spawns them in the task as today. There's never a second task on the same EFS.
- **Instance-aware locks (S3):** `data/locks.py`, plus the lock helpers in `data/prompts.py` and `data/rubrics.py`, record `instance`: the ECS task id from `ECS_CONTAINER_METADATA_URI_V4`, or the hostname locally. A lock from another instance is stale, which is safe because there's only one instance.

**ADR-5: the image is pinned, and fetches nothing at run time.**
- **Base and dependencies:** `python:3.12-slim`, linux/arm64, `uv sync --frozen --no-dev`.
- **RODA:** the RODA cache is filled at build time, and the task sets `UV_OFFLINE=1`. Spike S-2 proves it; the fallback installs RODA into the venv.
- **Web app and exclusions:** `web_dist` as committed. `.dockerignore` keeps out `runs/`, `.venv`, `node_modules`, `web/` and `.git`.
- **Build:** CDK `ContainerImage.from_asset` (#8). `cloud.sh` checks for Docker or Finch first.

**ADR-6: what lives where. Data in its own stack, so destroy and redeploy keep it (gate #9).**

| Stack | Holds | `cloud.sh destroy` |
|---|---|---|
| `FoundryStack` (existing) | runs bucket, access logs, `HarnessRole` | untouched |
| `FoundryDataStack` (new) | VPC (no NAT, no endpoints, so free when idle), EFS, its access point and backup plan, the token secret, the SNS alert topic and its email subscription | untouched |
| `FoundryAppStack` (new) | ECS cluster and service, task definition and roles, ALB, CloudFront, VPC origin, the paused-page bucket (its OAC policy names the distribution), probe Lambda, alarm, log group | destroyed |

- Because the data stack is never destroyed by `cloud.sh`, the next deploy mounts **the same** EFS and reads **the same** secret, with no import step (D4).
- `cloud.sh destroy --everything` is the documented way to remove the data too. It asks for the stack name typed back, and EFS, the secret and the backups are `RETAIN` even then.

| Path in the task | Backing |
|---|---|
| `/app`: code, `config/`, `fixtures/`, `web_dist` | image |
| `/data/runs` | EFS (the system of record). Mirrored to S3 per config. |
| `/data/project/tasks` | EFS |
| `/data/project/config`, `fixtures` | symlinks to `/app` (`FOUNDRY_HOME=/data/project`) |

- **EFS:**
  - **Elastic throughput** (per-use pricing, no burst-credit starvation on a 5 MB file system)
  - encrypted at rest and in transit
  - an access point for uid/gid 1000
  - IAM authorization
  - AWS Backup daily, 35 days
- **Board latency on NFS:** the board reads about 3 files per run on each poll. **Spike S-7** measures `/api/runs` on EFS with the real 77 runs. If p50 exceeds 500 ms, `foundry.data` gets an mtime-keyed in-process cache for `read_run` and `read_scores` (one process, so it's coherent).

**ADR-7: access tokens, with no AWS identity.**
- **Rejected:**
  - Cognito through the ALB: needs our own certificate, so a domain (#4).
  - Cognito Hosted UI: browser-only, so the CLI would need a device flow.
  - IAM: ruled out by the user.
- **Token:** `fdy_<32 random bytes, base64url>`, 256 bits.
- **Name:** `[a-z0-9-]{1,32}`, which avoids every delimiter (gate question).
- **The secret** (Secrets Manager) holds `{"session_key": "<64 random bytes, b64>", "tokens": {"<name>": "<sha256 hex of token>"}}`.
  - **CDK creates it once,** with `generate_secret_string` for `session_key` alone (`secret_string_template='{"tokens":{}}'`, `generate_string_key="session_key"`), and `RETAIN`.
  - **The template is pinned:** a unit test (`tests/test_infra.py`) asserts the synthesized `GenerateSecretString` block. Any change to it would make CloudFormation regenerate the value and wipe every token, so the test fails until the change is deliberate (gate question).
  - **From then on only `cloud.sh token add/list/revoke`** reads and writes it, with `PutSecretValue` (the operator's own AWS credentials).
  - **The SNS alert topic and its email subscription live in the data stack** (pass 2), so a destroy and redeploy of the app keeps alerts confirmed.
- **Reading the secret in the app** (layers, gate #11):
  - `data/secrets.py` fetches it with boto3 and caches it for 60 s. The data layer may use boto3, as `data/storage.py` does.
  - `services/access.py` wraps it: `check_token(token) -> name | None`, `session_valid(name, digest) -> bool`, `session_key()`.
  - `api/auth.py` imports only services, which is what `ALLOWED["api"]` permits.
- **When Secrets Manager fails (gate question):**
  - A failed fetch keeps serving the last good copy for up to 10 minutes, logging each failure.
  - After that, and at a start with no good copy, auth **fails closed**: `/api/*` answers **500** `{"error": "Sign-in is unavailable; try again shortly"}`. Never 503, which the front door reserves for "paused".
  - Revocation therefore takes at most 60 s normally, or 10 minutes during an outage, which the spec's A1 is updated to state.
- **Credentials:** the `X-Foundry-Token` header (CLI and scripts), or the `fdy_session` cookie (browser).
- **The cookie:**
  - Value: `<name>.<expiry>.<HMAC-SHA256(session_key, name|expiry|token_sha256)>`.
  - It binds to the token's **hash**, so revoking `alice` and re-adding `alice` with a new token kills the old sessions (gate question).
  - `HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=43200`.
- **Endpoints:**
  - `POST /api/auth/login {token}`:
    - **CSRF:** it's exempt from the CSRF check, since no session exists yet (gate #13).
    - **What still protects it:** the guard's Origin check (which must equal the public URL when an Origin is sent), JSON-only Content-Type, and `SameSite=Strict`.
    - It's rate-limited.
    - It returns 204 and sets the cookie.
  - `POST /api/auth/logout`: clears the cookie (CSRF required).
  - `GET /api/auth/me`: returns `{name}`.
- **Guard order** (gate #3):
  1. `GET /healthz`, exact path: 200 `ok`, before **any** check, so ALB, container and probe health checks never meet the Host rule.
  2. Host allowlist: the public host plus loopback (for the container's own health check and the operator's ECS Exec shell). Otherwise 400.
  3. Auth (when on), for `/api/*` except `/api/auth/login`: 401 `{"error": "Sign in required"}`, or **500** when the token store is unavailable, as above.
  4. Origin plus CSRF on writes, unchanged; login skips CSRF only.
- **Rate limit (A6):**
  - **Limited:** sign-in attempts **and** requests carrying a wrong `X-Foundry-Token`, 10 per minute per client address.
  - **The client address** comes from `CloudFront-Viewer-Address` (ADR-2); locally the peer address.
    - That header is `IP:port` (adding-cloudfront-headers.html), so the key is `rsplit(":", 1)[0]`, which also works for unbracketed IPv6. IPv6 addresses are keyed by their /64 (pass 2 #6).
  - **Storage:** an in-memory token bucket, since there's one instance. Beyond it, 429 `{"error": "Too many attempts; wait a minute"}`.
- **Never logged:** the guard never logs request headers. Errors log the route and status only. The uvicorn access log stays off.

**ADR-8: the spend cap is a ledger, plus reservations (A5, gate #12).**
- **`data/spend.py` (new):** `runs/.spend.jsonl`, append-only, one line per finished cost-bearing job: `{"month": "2026-10", "kind": "run|evaluate|draft", "id": …, "usd": …, "at": …}`. Appends happen under a lock file (single instance).
- **Two lines per job, so a crash can't lose spend** (pass 2 #4):
  - **At spawn,** under `ws.lock`, `jobs.spawn` appends an **open** line with the job's reservation, before the process starts.
  - **At exit,** the watcher appends the **close** line with the actual cost, *before* it sets `exit_code`, so the reservation is never dropped before the cost is booked.
  - **The actual cost is:**
    - **a run:** the run's step costs (`stats.costs`)
    - **an evaluation:** `scores.json` `evaluation.cost_usd`, read at completion, so re-scoring adds again
    - **a batch:** one close line, the sum of `summary.csv` `cost_usd`, which already includes each run's evaluation (no double count)
  - **At startup,** every open line with no close line (a crash, OOM or forced deploy) gets a close line charging its full reservation.
  - **Layers:** the watcher writes through `data.spend`. `services/spend.py` imports `jobs`, never the reverse, which keeps the services graph acyclic.
- **Rubric drafts:** run synchronously, so `ui_create_draft` registers a `draft` pseudo-job in `ws.jobs` (`alive=True`, no process) together with its open line under `ws.lock`. It then makes the model call **without** holding the lock, closes the line with `usage.cost_usd`, and removes the pseudo-job.
- **A job with no recorded cost** (a crash, or an unpriced model) is charged its reservation.
- **Reservations are computed from the config's own limits and prices** (spec A5):
  - **a run:** `max_input_tokens` × input price + `max_model_calls` × `max_tokens` × output price, about $1.87 today
  - **an evaluation:** the judge's and the summarizer's `max_tokens` × output price, plus their input bound (the view's character cap / 3.5 × input price)
  - **a draft:** the drafting model's `max_tokens` × output price, plus its source cap × input price
  - **a batch:** `max_cost + concurrency × (run + evaluation when scoring)`, less the batch's spend so far (pass 2 #5: a batch checks its cap only before each job)
  - **A model without a price** can't start while a cap is set.
- **`services/spend.py`:**
  - `month_to_date(ws)` reads only the ledger: the close lines of the current UTC month, plus the reservation of every open line with no close line. It never adds `ws.jobs` again, so nothing is double-counted.
  - `check(ws, extra)` refuses with 409 if `month_to_date + extra > cap`.
- **It's called under `ws.lock`,** together with the spawn. `extra` is always the **computed** reservation from the list above:
  - `runs.start_run`: the run's reservation from its config
  - `runs.start_evaluate`: the evaluation's, from the eval config. It checks **before** taking `.evaluating`, or releases that lock when the check refuses.
  - `batches.start_batch`: `max_cost + concurrency × (worst variant's run + evaluation when scoring)`. The missing-price rule covers every derived variant and the eval config.
  - `rubrics.ui_create_draft`: the drafting model's reservation
- **Locking:**
  - `ws.lock` is a non-reentrant `threading.Lock` that the callers already hold, so `jobs.spawn` appends the open line **without** taking it again.
  - A draft's pseudo-job is removed, and its close line written, in a `finally`, so a failed draft never stays alive. `Job.proc` becomes optional, and the `/api/jobs` `kind` adds `"draft"`.
- **Booking evaluation costs:** an evaluation that exits non-zero, or whose `scores.json` is older than the job, is charged its reservation, never a previous scoring's cost.
- **Stated gaps** (the margin is a +10% factor on every reservation):
  - `max_input_tokens` is enforced on a projection, so one call can overshoot it.
  - Calls billed after a client timeout, with up to 2 retries, are never recorded.
  - Inside a batch, a child evaluation that failed after spending records nothing.

  So concurrent starts serialize, and the check sees each other's reservations.
- **The bound:** with every reservation at its computed worst case, the ledger can't be overshot by concurrent starts. The only gap left is a model whose real price exceeds its configured price.
- **Locally,** with no `--monthly-cap-usd`, nothing is checked. The ledger is still written, but **seeding excludes `.spend.jsonl`**: laptop spend may belong to another account and doesn't count toward the cloud cap.

**ADR-9: the remote CLI, `foundry remote` (`foundry/cli_remote.py`).**
- **Layer:** added to `ENTRY` in `tests/test_architecture.py` (gate #11).
- **Transport:** stdlib `urllib`.
- **Typing:** responses are parsed with `foundry.api.schemas`.
- **Config:** `~/.config/foundry/remote.json` (0600), or `FOUNDRY_URL` and `FOUNDRY_TOKEN`.
- **Writes:** it fetches `/api/session` for the CSRF token, then sends it.

| Command | Calls |
|---|---|
| `login <url> <token>` | `GET /api/auth/me`, then saves |
| `runs` | `GET /api/runs` |
| `run show <id>` | `GET /api/runs/{id}` |
| `run start --prompt --config [--wait]` | `POST /api/runs`, polling until done |
| `evaluate <id> [--wait]` | `POST /api/runs/{id}/evaluate` |
| `batch start … [--wait]`, `batch show`, `batches` | the batch routes |
| `compare <a> <b>` | `GET /api/compare` |
| `import <file> [--prompt-id] [--config]` | `POST /api/import` |
| `jobs` | `GET /api/jobs` |

- **Output and exit codes:** text by default, `--json` for raw. Exit 0, 1 on an API error, 2 on a usage error.

**ADR-10: interrupted runs can be scored remotely (S5, gate #8).**
- **Ownership by a run lock** (pass 2 #3):
  - `foundry run` holds `<run_dir>/.running` for its whole life, an instance-aware lock like `.evaluating` (`data/locks.py` gains a `kind` argument). Batch children and runs started from an ECS Exec shell are covered too.
  - A run is **orphaned** when `run.json` says `running` and no live `.running` lock from this instance exists.
- **The service:** `services/runs.start_evaluate` accepts an orphaned run and passes `--force` to `foundry evaluate`, as the CLI advice does today.
- **A run that is genuinely live** still gets 409.
- **The API:** `RunDetail` gains `orphaned: bool`, a schema change, so `types.ts` is regenerated.
- **The UI:** the run panel shows "This run was interrupted (the server restarted). Score what it recorded" with the Score button. The CLI's `evaluate` works the same.

**ADR-11: the region stays us-west-2 (gate #10).** The configs' Bedrock `region`, the IAM profile ARNs and NAIP all assume it, so `--region` is dropped from the spec (a non-goal).

**ADR-12: `FOUNDRY_S3_URI` applies in the kernel (gate question).** `foundry.config.load_config` overrides `[storage] s3_uri` when the variable is set. So the UI, its subprocesses (run, evaluate, batch) and the batch's own children all mirror to this stack's bucket.

**ADR-13: the receiver is off in the cloud (I7, gate question).** `foundry ui --no-receiver` makes `services/imports.receiver_start` raise 409 "Not available in this deployment; import trace files instead". The UI's Import page hides the live receiver section when `/api/session` reports `receiver: false`.

## The web UI change (Product Designer: reviewed with the user before Build)

**Sign-in** reuses `PageHeader` and the form styles, and the header gains "Signed in as `<name>` · Sign out".

```
┌──────────────────────────────────────────────────────────┐
│  [TF logo]  Foundry eval sandbox                          │
│                                                           │
│  Sign in                                                  │
│  Paste the access token the operator gave you.            │
│  ┌──────────────────────────────────────────────┐         │
│  │ fdy_…                                        │         │
│  └──────────────────────────────────────────────┘         │
│  [ Sign in ]                                              │
│  "That token isn't valid."  ·  "Too many attempts; wait a minute."  ·  "Sign-in is unavailable; try again shortly." │
└──────────────────────────────────────────────────────────┘
```

**Other screens:**
- the run panel's interrupted-run note (ADR-10)
- the paused page (static HTML in the house style)
- the spend-cap 409 in the existing error banner

**Behaviour:** any 401 sends you to sign-in, and back to the page afterwards. Locally, sign-in never appears.

## Infrastructure detail

| Resource | Settings |
|---|---|
| VPC (data stack) | 2 AZs. Public subnets (task), private-isolated subnets (ALB, VPC-origin ENIs). No NAT, no endpoints. The internet gateway is present (needed by VPC origins and by the task's egress). |
| ECS (app stack) | Container Insights off. The alarm uses the probe, not task count (gate #6). Fargate service: desired 1, min 0 / max 100, circuit breaker with rollback, `enable_execute_command=True`, public IP, platform LATEST. |
| Task definition | ARM64, 1 vCPU / 3 GB. Container health check: GET `127.0.0.1:8765/healthz`. `initProcessEnabled`. |
| Environment | `FOUNDRY_HOME=/data/project`, `FOUNDRY_S3_URI`, `PUBLIC_URL` (the distribution's domain; the task depends on the distribution, never the reverse), `AUTH_SECRET_ARN`, `MONTHLY_CAP_USD`, `UV_OFFLINE=1` |
| Task role | today's `HarnessRole` statements, generated the same way. Plus `secretsmanager:GetSecretValue` on the one secret, EFS `ClientMount`/`ClientWrite` on the access point, `s3:GetObject` and `s3:DeleteObject` on `seed/*`, and the ECS Exec `ssmmessages` actions. |
| ALB | internal, HTTP :80 → 8765, health check `/healthz`, idle timeout 120 s, deregistration delay 10 s |
| Probe (O2, gate #6) | **Lambda** (Python, 128 MB) on an EventBridge 5-minute schedule. It GETs `https://<dist>.cloudfront.net/healthz` with a 10 s timeout and puts a `Foundry/Probe Up` metric: 1 only for a 200 whose body is `ok`. The alarm fires on `Up < 1` for 2 of 2 periods, with missing data treated as breaching, and goes to the data stack's SNS topic. |
| Paused (gate #6) | `cloud.sh stop` disables the alarm's actions. `start` runs `set-alarm-state OK` and then re-enables them, so a failed start still changes state and emails (pass 2 question). |
| Budget | only with `--budget <usd>`: account-wide monthly, at 80% and 100%, to the same email (gate #7). Documented as account-wide. |
| Logs | `/foundry/app`, 30 days |
| Outputs | `Url`, `SecretArn`, `ClusterName`, `ServiceName`, `RunsBucketName`, `AlarmName` |

**`cloud.sh`:**

| Command | Does |
|---|---|
| `deploy --email … [--cap 30] [--budget USD] [--force]` | Checks tools (Docker/Finch, Node, uv, credentials, bootstrap). Refuses while `/api/jobs` shows live jobs (using the operator token in `~/.config/foundry/remote.json`), unless `--force`. Deploys all three stacks, prints the URL, and on first deploy creates an `operator` token. |
| `token add <name>` / `list` / `revoke <name>` | Secret edits. `add` prints the token once. |
| `seed [--runs runs/ --tasks tasks/]` | S4 |
| `stop` / `start` | desired count 0/1, plus alarm actions off/on |
| `logs [-f]`, `shell` | `aws logs tail`; ECS Exec |
| `destroy [--everything]` | ADR-6 |

**Docs:** `docs/deploy.md` covers the fresh-account path, operating it, and the restore procedure (O4). The README links to it.

## Seeding (S4)

1. `cloud.sh seed` tars `runs/` (without `.ui-jobs`, locks, `.uploads` or `.spend.jsonl`) and `tasks/`.
2. It uploads the tarball to the fixed key `s3://<runs bucket>/seed/pending.tgz`, then runs `update-service --force-new-deployment`. No task-definition change, so no drift against D3 (pass 2 question).
3. The entrypoint unpacks `seed/pending.tgz` only into an empty `/data/runs`, then deletes the key (the task role gets `s3:GetObject` and `s3:DeleteObject` on `seed/*`). If runs exist, it logs "seed skipped: the deployment already has runs", leaves the key, and `cloud.sh` reports it.

## Deploys and in-flight work (S5)

- `cloud.sh deploy` refuses while jobs are live, unless `--force`.
- After a forced deploy or a crash, ADR-10 lets any user score what an interrupted run recorded.
- A batch shows "stopped" (existing behaviour).

## App changes, by layer (the architecture rules hold; gate #11)

| Layer | Change |
|---|---|
| kernel | `config.load_config`: the `FOUNDRY_S3_URI` override |
| data | `secrets.py`; `spend.py` (the ledger); instance-aware locks (`locks.py`, `prompts.py`, `rubrics.py`); the optional mtime cache (S-7) |
| services | `access.py`; `spend.py`; `Workspace.monthly_cap_usd` and `Workspace.receiver_enabled`; the spend checks in `runs`, `batches` and `rubrics`; the ledger appends in the `jobs` watcher and in `rubrics`; ADR-10 in `runs.start_evaluate`; `runs.detail` adds `orphaned`; `imports.receiver_start` honours `--no-receiver` |
| api | `auth.py`; guard order; `--public-url` Host and Origin; `/healthz`. Schemas: `AuthMe`, `LoginBody`, `RunDetail.orphaned`, `Session.receiver`; regenerate `types.ts` |
| entry | `cli.py ui`: `--host --public-url --auth-secret --monthly-cap-usd --no-receiver`, and keep-alive 130 when not on loopback. `cli_remote.py`. `ENTRY` adds `foundry.cli_remote`. |
| web | `SignIn`; 401 handling in `api.ts` and `usePoll`; "Signed in as"; the interrupted-run note; the Import page hides the receiver when it's off |
| infra | `FoundryDataStack`, `FoundryAppStack`; `tests/test_infra.py` (synth assertions: the VPC origin is `HTTP_ONLY`/80, the ALB is internal, the secret template is pinned, no NAT, RETAIN on the data) |

**Tests (new):**
- **Auth:**
  - the hash matches
  - revocation within the cache window, with a mocked clock
  - the 10-minute stale window, then 500 (never 502–504)
  - a cookie with a bad HMAC, past its expiry, or from a re-added name gets 401
  - login is exempt from CSRF but not from Origin
  - rate limit per client address
  - 401 sweep over every route
  - local mode has no auth
- **Guard:** `/healthz` before Host; public Host and https Origin only when configured.
- **Spend:**
  - the ledger on run, evaluation, re-score, batch and draft
  - month boundary by finish time
  - reservations
  - concurrent starts serialized
  - charged at reservation when no cost is recorded
- **Locks:** a foreign instance's lock is stale.
- **ADR-10:** an orphaned run is scorable with `--force`; a live one gets 409.
- **Remote CLI:** against a real local uvicorn, covering login, runs, run start `--wait` (offline), batch, import, `--json` and the exit codes.
- **Infra:** synth assertions.
- **Vitest:** sign-in, the 401 redirect, the three error messages, the interrupted note.
- **The existing e2e:** unchanged in local mode. `scripts/cloud_smoke.sh` runs against the deployed URL.

## Cost (us-west-2, Price List API 2026-10-31, #6)

| Item | Running | Stopped | Destroyed (app stack) |
|---|---|---|---|
| Fargate ARM: 1 vCPU / 3 GB, 730 h | $31.4 | $0 | $0 |
| Task public IPv4 | $3.7 | $0 | $0 |
| Internal ALB: $0.0225/h + about 1 LCU | $22.3 | $22.3 | $0 |
| CloudFront (inside the always-free tier), paused-page bucket | ≈ $0 | ≈ $0 | $0 |
| VPC origin | **unverified, assumed $0;** checked against the pricing page at Verify | | |
| EFS (Elastic, < 1 GB at $0.30/GB-month) and backups | < $1 | < $1 | < $1 |
| Secrets Manager: 1 secret | $0.40 | $0.40 | $0.40 |
| Probe Lambda (8.6k invocations), 1 alarm, logs | ≈ $1 | ≈ $0.5 | $0 |
| **Fixed per month** | **≈ $59** | **≈ $24** | **≈ $1.4** |

Within the spec's C1 limits: running ≤ $65, paused ≤ $30, destroyed ≤ $2. Bedrock is capped by A5 at $30 a month by default.

## Security review

- **Surface:**
  - One HTTPS hostname.
  - The ALB is internal and admits only CloudFront's origin-facing ranges; the task admits only the ALB.
  - The unauthenticated paths: the SPA shell, `/assets/*`, `/healthz`, `/paused.html` and `POST /api/auth/login` (rate-limited, Origin-checked).
- **Auth:**
  - 256-bit tokens, stored hashed, compared in constant time, and revocable.
  - Sessions are HMAC-bound to the token hash.
  - CSRF and Origin stay on for cookie writes; the CLI's header token can't be ridden by CSRF.
  - Auth fails closed when its store is unreachable.
- **Abuse:** the ledger cap counts re-scores, drafts, crashes and unpriced models (ADR-8). Starts are serialized.
- **Secrets:** no headers are logged, nothing sensitive is in the image (gitleaks over the build context in `cloud.sh`), and the secret's template is pinned by a test.
- **Verify:** a pentest probe, scoped with the user, tries:
  - unauthenticated route access
  - brute force against the rate limit (including a spoofed `CloudFront-Viewer-Address`)
  - cookie forgery and replay after a re-add
  - Host spoofing
  - direct ALB access
  - spend-cap bypass by concurrent starts and re-scores

## Phase 2 (not built)

- S3 and DynamoDB for data and locks.
- ECS `RunTask` and Step Functions for jobs.
- Two or more API tasks.
- **Triggers:** many users, uptime needs, or jobs that must survive deploys.

## Spikes before Build (riskiest first)

| Spike | Proves | Fallback |
|---|---|---|
| S-5 | the end-to-end front door from CDK 2.275.0 works: VPC origin `HTTP_ONLY` to the internal ALB to the task. Also that `AllViewerAndCloudFrontHeaders-2022-06` forwards Host, cookies and `X-Foundry-Token`, that CloudFront overwrites a spoofed `CloudFront-Viewer-Address`, and that the prefix-list security group rule fits the quota. | a public ALB restricted to the prefix list, plus a secret origin header checked by a listener rule; `X-Forwarded-For` for the client address |
| S-4 | rubric drafting finishes in under 60 s on Bedrock | asynchronous drafting (202 plus polling); otherwise the read timeout goes up to 120 s, within the quota |
| S-7 | `/api/runs` on EFS with 77 runs: p50 under 500 ms | the mtime-keyed read cache in `foundry.data` |
| S-2 | RODA starts offline from uv's cache | install RODA into the venv |
| S-6 | the arm64 image builds on the laptop, and the suite passes inside it | an x86 build |

## AC → component map

| AC | Component |
|---|---|
| D1–D5 | `cloud.sh`, the three stacks, ADR-11, ADR-12; D5 measured at Verify |
| I1 | CloudFront, ALB, task, `SignIn` |
| I2 | `api/auth.py`, guard order |
| I3 | `cli_remote.py`, installed from a clone (the repo stays private) |
| I4 | auth, cap and receiver flags all off by default |
| I5 | ADR-5 |
| I6 | paused page |
| I7 | ADR-13 |
| A1 | ADR-7 secret plus `cloud.sh token` |
| A2 | ADR-7 cookie |
| A3 | ADR-2 SG chain, Host allowlist |
| A4 | task role, `.dockerignore`, gitleaks |
| A5 | ADR-8 |
| A6 | ADR-7 rate limit |
| S1 | EFS and backup |
| S2 | ADR-4 |
| S3 | instance-aware locks |
| S4 | seeding |
| S5 | `cloud.sh deploy` check, ADR-10 |
| O1 | log group |
| O2 | probe, alarm, optional budget |
| O3 | the remote CLI's batch commands |
| O4 | circuit breaker, redeploy, AWS Backup, the `deploy.md` restore run at Verify |
| C1 | cost table |

## One-way doors

- **Deleting EFS, the backups or the secret** would lose data and tokens. Only `destroy --everything`, with the name typed back, gets close, and even it leaves `RETAIN` resources in place.
- **Publishing the URL** is the user's exposure decision (2026-10-31).

## Gate record

**Design gate pass 1 (2026-10-31): 13 blockers, all resolved above:**

| # | Blocker | Fix |
|---|---|---|
| 1 | HTTPS to an HTTP ALB | `HTTP_ONLY`/80 |
| 2 | the service SG doesn't exist until the VPC origin does | the origin-facing prefix list |
| 3 | health check against the Host check | `/healthz` first |
| 4 | keep-alive | uvicorn 130 s |
| 5 | rate-limit key | the 2022-06 policy, plus the S-5 spoof check |
| 6 | alarms | the probe Lambda, paused-aware |
| 7 | tag budget in a member account | account-wide and optional |
| 8 | can't score an interrupted run | ADR-10 |
| 9 | destroy orphans the data | the data stack |
| 10 | region | fixed at us-west-2 |
| 11 | layers | `services/access.py`; `cli_remote` in `ENTRY` |
| 12 | cap bypasses | the ledger at finish time |
| 13 | login CSRF | exempt, with Origin, JSON and SameSite |

**Its six questions were answered** in ADR-7 (Secrets Manager failure; re-add and name charset; secret pinning), ADR-12 (`FOUNDRY_S3_URI` in the kernel), ADR-2 and S-7 (EFS throughput and latency), and ADR-13 (the receiver).

**Design gate pass 2 (2026-10-31): six blockers, all resolved above:**

| # | Blocker | Fix |
|---|---|---|
| 1 | paused page swallowing app errors | the app never sends 502–504; 503 pinned; a `/paused.html` behaviour; the probe checks for `ok` |
| 2 | stack cycle | paused bucket in the app stack; EFS rule written as `service.connections.allow_to_default_port(fs)`; `tests/test_infra.py` asserts the data stack has no `Fn::ImportValue` and no dependency on the app stack |
| 3 | live batch runs seen as orphaned | the `.running` run lock |
| 4 | ledger losing crashes | open and close lines, plus reconciling at startup |
| 5 | batch reservation | includes the `concurrency` overshoot |
| 6 | rate-limit key including the port | strip it; IPv6 /64 |

**Pass 2's questions, answered:**
- **Alarm after `start`:** `set-alarm-state OK` first.
- **SNS:** the topic moves to the data stack.
- **URL after destroy and redeploy:** a new `*.cloudfront.net` URL is accepted and documented under D4. `cloud.sh deploy` rewrites the operator's `remote.json` URL and prints the new one; users run `foundry remote login` again.
- **A3's forged-Host test:** CloudFront rejects a Host it doesn't serve, so Verify tests the app's Host rule inside the task (`cloud.sh shell`, `curl -H 'Host: evil'` → 400) and CloudFront's own rejection separately.
- **Seeding:** the fixed key, with no task-definition change.
- **Drafts:** a pseudo-job holds the reservation.
- **Batch ledger:** one line per batch from `summary.csv`. A user's re-score during a batch adds its own line: a real extra spend, conservatively counted.
- **Seeded spend history:** excluded.
- **Data-stack export changes:** accepted. The data stack rarely changes; `docs/deploy.md` documents the two-step update (deploy the app stack without the import, then the data stack, then the app stack again).
- **`destroy --everything` and the managed VPC-origin security group:** the app stack's destroy removes the VPC origin first, AWS then deletes its managed group (asynchronously), and `cloud.sh` retries the data stack's deletion until the group is gone, for at most 20 minutes. This is listed as a spike check (S-5).

**Design gate pass 3 (2026-10-31): three blockers, all stale text contradicting pass 2's fixes, now corrected:**
1. The auth-outage status is 500 everywhere: guard order, tests and spec A1.
2. The spend-check calls use the computed reservations, including the batch's `concurrency` term.
3. The stack table puts the paused bucket in the app stack and the SNS topic in the data stack.

The reviewer synthesized a prototype of both stacks with aws-cdk-lib 2.275.0:
- the data stack has no `Fn::ImportValue`
- the EFS ingress rule lands in the app stack
- the app stack depends only on the data stack

The prefix list `pl-82a045eb` fits the security group's quota of 60 (AWS CLI).

**Pass 3 minors folded into Build:**
- **Errors and timeouts:**
  - The "no 502–504" test covers `ui/spa.py`'s missing-bundle response, which becomes 500.
  - `cli_remote` reports a non-JSON 503 as "the sandbox is paused or restarting".
  - S-4 covers the draft API path end to end.
- **Run state:**
  - `.running` is taken before `run.json` is first written (`runner.py:59`).
  - Orphaned runs leave the Running column (`runs.cards` and `board_column`): a new headline "interrupted" in the attention column.
- **Wording and inputs:**
  - The secret's `session_key` is generated with `password_length=64` and `exclude_punctuation`, which is what the pinned-template test locks in.
  - The lock's `instance` comes from the container id in `ECS_CONTAINER_METADATA_URI_V4`; it's unique per task.
  - A draft's input bound uses the model's context window, since binary sources aren't character-bounded.
- **For the pentest:**
  - DNS rebinding on the rubric URL fetcher (https only)
  - subprocesses inheriting the task role's secret read
