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
     cd infra && npx cdk diff
     cd infra && npx cdk deploy
     ```
     Expect: the diff shows only additions (1 bucket, 1 bucket policy, 1 role, 1 policy, metadata). Note the `RunsBucketName` and `HarnessRoleArn` outputs.

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
     cp -r fixtures/runs/injection /tmp/inj
     uv run foundry evaluate /tmp/inj --config config/bedrock-live.toml
     ```
     Expect: at least one criterion in `scores.json.output.criteria` scores below its `max`.

  8. **Record the results** of L1–L5 in `docs/eval-sandbox/handoff.md`, under *Exercised live*.

- **Compatibility window:** none. Offline and live share one code path, and no stored data changes format. Run directories carry `contract_version: "0.2"`.

## Rollback

| What to undo | Steps | Reversible? |
|---|---|---|
| A bad live run or evaluation | Nothing to roll back: runs are separate directories. Delete `runs/<run_id>/` locally. In S3, keep the object versions or remove them with `aws s3 rm --recursive s3://<bucket>/runs/<run_id>/`. | Yes |
| Live mode itself | Stop using `config/bedrock*.toml` and go back to `config/offline.toml`. No code change is needed. | Yes |
| Infrastructure | `cd infra && npx cdk destroy`. This removes the role and policy. The **bucket is retained** (`RemovalPolicy.RETAIN`, on purpose: run history is provenance). To delete it too: `aws s3 rb s3://<bucket> --force`, after `aws s3api delete-objects` for every object version, because the bucket is versioned. | Deleting the bucket is **irreversible**; do it only on purpose |
| Code | `git revert <commit>` on `prototype` (one commit per slice; see `git log 605627f..prototype`), or `git switch main`. | Yes |

## Observability

This is a local, single-user tool. There are no servers to page on. These signals cover the failure modes in the design:

- **Per-run outcome**
  - Where: `run.json.status`, the UI cards, and the exit codes (0, 1, 2)
  - Act when: a run fails or times out. Read `trace.jsonl`, the report's *Struggles*, and `mcp-<server>.log`.
- **Cost per run**
  - Where: `scores.json.workflow.cost_usd` and `scores.json.evaluation.cost_usd`. These are null for Nova until a price is added to `[prices]`.
  - Act when: a run costs more than **$2.00**, the spec ceiling. Stop live runs and check `max_*` in `[limits]`.
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

**Not in place yet:** an AWS Budgets alarm for the sandbox account. Set one before running many live runs (follow-up).

## Communication

- [x] No PR, by the user's instruction. The commits on `prototype` describe each slice and its ACs.
- [x] CHANGELOG: `CHANGELOG.md`, 0.1.0.
- [x] User docs: README (quickstart, model and tracer swap, replay, bringing your own task, deploy, tests, K-5 decisions).
- [x] Trace contract updated to v0.2 (additive) in `docs/trace-contract.md`.
- [ ] Tell the team that `prototype` is ready, and point them at the README quickstart. The default branch `main` doesn't have this work.

## Known limitations

- **Offline mode doesn't evaluate a real agent.** The scripted model replays fixed decisions, so offline runs prove the pipeline, not agent quality. Real behavior needs L1–L3.
- **The rubric is a stand-in.** `adb-rubric-v0` is ours, not Clay's (K-1). The judge hasn't been calibrated against human scores (follow-up D-6).
- **Bedrock prices aren't verified against the console** (design ADR-7), and **Nova's price is unknown**, so evaluation `cost_usd` is null under `bedrock.toml`.
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
