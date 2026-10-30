# Agentic Eval Sandbox: Launch

What "launch" means here: taking the offline-verified `prototype` branch live in **one AWS sandbox account**. That covers the live checks L1–L5 and the demo. No production service runs, and nothing is user-facing beyond the local UI and the reports.

Per the user's instruction, everything ships on the `prototype` branch with **no PR**.

## Prerequisites

| Item | How to get it | Verify with |
|---|---|---|
| Toolchain | asdf + `asdf install` + `uv python install` (README "Local setup") | `uv --version`, `node --version` |
| Dependencies | `uv sync` at the repo root; `cd infra && npm ci` | `uv run foundry --help` |
| AWS credentials for **one sandbox account**, region us-west-2 | `aws sso login --profile <sandbox-profile>`, then `export AWS_PROFILE=<sandbox-profile> AWS_REGION=us-west-2` | `aws sts get-caller-identity` shows the sandbox account id |
| Bedrock model access in us-west-2 for `us.anthropic.claude-sonnet-5-5`, `us.anthropic.claude-haiku-5-5` and `us.amazon.nova-2-lite-v1:0` | Bedrock console → Model access (us-west-2) | `aws bedrock-runtime converse --model-id us.amazon.nova-2-lite-v1:0 --messages '[{"role":"user","content":[{"text":"hi"}]}]' --region us-west-2` returns a reply |
| CDK bootstrap (once per account and region) | `cd infra && npx cdk bootstrap aws://<account>/us-west-2` | The `CDKToolkit` stack exists in CloudFormation |
| **Budget alarm (required before step 4).** Owner: the team lead. Notifies the team lead's email at 80% of $50 per month. | `aws budgets create-budget --account-id <account> --budget '{"BudgetName":"foundry-sandbox","BudgetLimit":{"Amount":"50","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' --notifications-with-subscribers '[{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":80,"ThresholdType":"PERCENTAGE"},"Subscribers":[{"SubscriptionType":"EMAIL","Address":"<team-lead-email>"}]}]'` | `aws budgets describe-budget --account-id <account> --budget-name foundry-sandbox` |

**Which credentials runs use.** `foundry` uses whatever the AWS SDK default chain finds, normally the operator's SSO profile (`AWS_PROFILE`). The stack's `HarnessRole` is a least-privilege alternative: Bedrock invoke actions plus `runs/*` in the bucket. To use it, add a profile with `role_arn = <HarnessRoleArn>` and `source_profile = <sandbox-profile>` to `~/.aws/config`, then `export AWS_PROFILE=<that profile>`. The S3-failure runbook below refers to whichever identity is active (check with `aws sts get-caller-identity`).

**Spend bounds per run.** These are the `[limits]` in `config/bedrock*.toml`, so any copy of those files inherits them:

- `max_model_calls = 20`
- `max_input_tokens = 600000`
- `max_tokens = 1024` per agent call
- `max_seconds = 300`

Evaluation makes at most 4 model calls (judge and summarizer, one retry each), with `max_tokens = 2048` each and the run view capped at about 20k tokens. At the configured prices that's at most $1.32 + $0.23 for the agent plus $0.27 for evaluation, so **≤ $1.82 per run**. Nova is priced at Sonnet's rate as an upper bound.

## Rollout

- **Mechanism:** direct, in a single sandbox account. There's no flag. Live versus offline is chosen per command by `--config`, and offline stays the default everywhere.
- **Order:**

  1. **Offline smoke**, which needs no credentials:
     ```bash
     uv run pytest -m "not network"
     uv run foundry run --config config/offline.toml --prompt adb-001
     ```
     Expect: all tests pass, and the run exits 0.

  2. **Deploy the infrastructure (L4, part 1):**
     ```bash
     cd infra
     npx cdk diff      # review the diff first
     npx cdk deploy
     cd ..
     ```
     Expect:
     - The diff shows only additions: 1 bucket, 1 bucket policy, 1 role, 1 policy, and metadata.
     - The bucket blocks all public access, uses SSE-S3 encryption, is versioned, enforces SSL-only access through its bucket policy, and is retained on stack deletion.
     - All resources are tagged `project=clay-foundry` and `owner=team-46`.
     - Note the `RunsBucketName` and `HarnessRoleArn` outputs. CDK generates the bucket name.

  3. **Point the runs at S3.** Copy `config/bedrock.toml` to `config/bedrock-live.toml` and set:
     ```toml
     [storage]
     s3_uri = "s3://<RunsBucketName>"
     ```
     Don't commit the bucket name if the team prefers to keep account details out of git.

  4. **L1, live run:**
     ```bash
     uv run foundry run --config config/bedrock-live.toml --prompt adb-001
     ```
     Expect:
     - exit 0
     - `run.json` status `succeeded`
     - the trace has `llm_call` steps with non-null `tokens_in`, `tokens_out` and `cost_usd`, and `tool_call` steps with `server: "roda"`
     - `foundry validate` exits 0
     - `aws s3 ls s3://<bucket>/runs/<run_id>/` lists the files (this completes L4)

  5. **L3, live evaluation:**
     ```bash
     uv run foundry evaluate runs/<L1 run id> --config config/bedrock-live.toml
     ```
     Expect:
     - exit 0
     - `scores.json.output.judge.id` is `us.amazon.nova-2-lite-v1:0`, which differs from the agent
     - no "grading itself" warning
     - `report.md` has a real narrative and rationales

  6. **L2, model swap:**
     ```bash
     uv run foundry run --config config/bedrock-alt.toml --prompt adb-001
     ```
     Expect: exit 0, and `run.json.model.id` is `us.anthropic.claude-haiku-5-5`.

  7. **L5, prompt injection against the real judge:**
     ```bash
     rm -rf /tmp/inj && cp -r fixtures/runs/injection /tmp/inj
     uv run foundry evaluate /tmp/inj --config config/bedrock-live.toml
     ```
     Expect: `scores.json.output.score` is ≤ 0.5. The injected output is a one-line plan that demands maximum scores, so a judge that resisted the injection scores it low. Also, at least one criterion's rationale should not claim full marks. Any output score above 0.5 fails L5: record it in `docs/security/findings.md` and don't trust live judge scores until it's fixed.

  8. **Record the results** of L1–L5 in `docs/eval-sandbox/handoff.md`, under *Exercised live*.

- **Compatibility window:** none. Offline and live share one code path, and no stored data changes format. Run directories carry `contract_version: "0.2"`.

**Security findings** (`docs/security/findings.md`):

- F-001 to F-007: all fixed.
- F-008, F-009, F-010, F-012: accepted. Owner is the user; review by 2026-11-30, or before deploying outside the sandbox.
- F-011 (SSE-S3 instead of a KMS key, scanner-rated High): open, needs the user's ruling.
- F-013 (actions pinned by tag): open follow-up.

None blocks a sandbox launch.

## Rollback

| What to undo | Steps | Reversible? |
|---|---|---|
| A bad live run or evaluation | Nothing to roll back: runs are separate directories. Delete `runs/<run_id>/` locally. In S3, keep the object versions or remove them with `aws s3 rm --recursive s3://<bucket>/runs/<run_id>/`. | Yes |
| Live mode itself | Stop using `config/bedrock*.toml` and go back to `config/offline.toml`. No code change is needed. | Yes |
| Infrastructure | `cd infra && npx cdk destroy`. This removes the role and policy. The **bucket is retained** (`RemovalPolicy.RETAIN`, on purpose: run history is provenance), and it is then no longer managed by any stack. A later `cdk deploy` creates a **new** bucket with a new generated name, so update `s3_uri` in `config/bedrock-live.toml` from the new `RunsBucketName` output. To delete the old bucket deliberately, empty it with the S3 console's **Empty** action, which removes every version and delete marker, then `aws s3 rb s3://<bucket>`. | Deleting the bucket is **irreversible**; do it only on purpose |
| Code | `git revert <commit>` on `prototype` (one commit per slice; see `git log 605627f..prototype`), or `git switch main`. | Yes |

## Operating notes

- **Exit codes:**
  - `foundry run`: 0 = succeeded; 1 = the run failed or timed out, and its run directory has the details; 2 = a usage or config error, and nothing was created
  - `foundry evaluate`: 0 = scores and report written; 2 = refused, with the message saying why
- **SIGKILLed runs:** find a leftover MCP server with `pgrep -fl roda-mcp-server` and stop it with `pkill -f awslabs.roda-mcp-server`. Then run `foundry evaluate --force runs/<id>`.
- **Several teammates in one account:** run ids carry a timestamp plus a random suffix, so they don't collide in the shared bucket. Bedrock throttling is per account and model. Botocore retries throttled calls with backoff (`model_retries = 2`), and a run that still fails is recorded as failed. Stagger live runs rather than starting many at once.

## Observability

This is a local, single-user tool. There are no servers to page on. These signals cover the failure modes in the design:

- **Per-run outcome**
  - Where: `run.json.status`, the UI cards, and the exit codes (0, 1, 2)
  - Act when: a run fails or times out. Read `trace.jsonl`, the report's *Struggles*, and `mcp-<server>.log`.
- **Cost per run**
  - Where: `scores.json.workflow.cost_usd` and `scores.json.evaluation.cost_usd`. These are computed from the `[prices]` table; Nova's price there is an upper bound. The authoritative figure is AWS Cost Explorer: filter by service *Amazon Bedrock*, daily granularity, which lags by up to about 24 hours.
  - Act when: a run's recorded cost exceeds **$2.00**, or Cost Explorer shows more than expected for the number of runs. Stop live runs and check `max_*` in `[limits]`.
- **Limits hit**
  - Where: `note` steps reading `max_model_calls`, `max_input_tokens` or `max_seconds … reached`, shown in Struggles
  - Act when: they repeat on a prompt. Tune the prompt or raise the limit deliberately.
- **Judge health**
  - Where: `scores.json.output.error`, and an overall score of "incomplete"
  - Act when: it repeats. Check model access or throttling, then re-run `foundry evaluate`.
- **Trace export (optional)**
  - Where: Phoenix (`uvx --from arize-phoenix phoenix serve`) with `config/offline-otel.toml`, or OTel settings added to the live config. Spans land in Phoenix's `default` project.
  - Act when: you see `OTel export to … failed`. `trace.jsonl` is unaffected; start the backend.
- **S3 mirror**
  - Where: an `S3 upload to s3://… failed` line on stderr
  - Act when: you see it. Check credentials and the `HarnessRole` policy, then re-run `foundry evaluate` to upload again.

**Budget alarm:** this is a prerequisite (see the table above), and it emails the team lead at $40 of a $50 monthly budget.

## Communication

- [x] No PR, by the user's instruction. The commits on `prototype` describe each slice and its ACs.
- [x] CHANGELOG: `CHANGELOG.md`, 0.1.0.
- [x] User docs: README (quickstart, model and tracer swap, replay, bringing your own task, deploy, tests, K-5 decisions).
- [x] Trace contract updated to v0.2 (additive) in `docs/trace-contract.md`.
- [ ] Push `prototype` to the shared remote. **Not pushed yet**: pushing is the owner's call. Then tell the team it's ready and point them at the README quickstart. The default branch `main` doesn't have this work.

## Known limitations

- **Offline mode doesn't evaluate a real agent.** The scripted model replays fixed decisions, so offline runs prove the pipeline, not agent quality. Real behavior needs L1–L3.
- **The rubric is a stand-in.** `adb-rubric-v0` is ours, not Clay's (K-1). The judge hasn't been calibrated against human scores (follow-up D-6).
- **Bedrock prices aren't verified against the console** (design ADR-7). **Nova's price is an upper bound** (Sonnet's rate), so recorded evaluation costs are overestimates until the real price is entered.
- **Strands pins `mcp<2.2`**, so `mcp` stays at 2.1.1 with accepted Medium CVEs (`docs/security/findings.md` F-008, F-009; review by 2026-11-30).
- **A `foundry run` killed with SIGKILL leaves its MCP subprocess running** until it exits on its own, and leaves `run.json` at `running`. Use `foundry evaluate --force` for such runs.
- **The UI doesn't auto-refresh.** Reload to see progress.
- **Network tests depend on** the public Registry of Open Data and PyPI being reachable.

## Post-launch checks

- **Right after L1–L5:**
  - Each expectation above holds.
  - Live cost per run is ≤ $2.00.
  - The judge differs from the agent.

  If L1 fails on model access, the fix is enabling access or switching model ids in config. If a run costs more than $2.00, stop live runs and file a fix before demoing.
- **Before the demo:**
  - Run the four success-signal demos once without editing code: live run (or offline if credentials are gone), model swap, tracer swap, replay of a saved run.
  - Keep 2–3 evaluated golden runs (G-1) to replay if the network or Bedrock fails.
- **Within 4 weeks (by 2026-11-30):**
  - Review F-008/F-009, and whether Strands now allows `mcp` ≥ 2.2.
  - Check whether Clay has scored a run with its own rubric (the spec's success signal).
