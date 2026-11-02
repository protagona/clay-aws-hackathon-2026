# Personal deployments in a shared account — Spec

**Track:** Quick   **Date:** 2026-11-01   **Status:** Built, live in the sandbox 2026-11-02 (see handoff.md). The user asked for it on 2026-11-01: "do option 2, per-participant deployments, with a bare option for a 'main' deployment".

## Problem

Hackathon participants share one AWS sandbox account, but every `scripts/cloud.sh deploy` creates or updates the same two stacks, `FoundryDataStack` and `FoundryAppStack`. So a second participant doesn't get their own deployment: they overwrite the shared one's code, alert email and spend cap, and their `destroy` removes it for everyone. Beyond the stack names, more is shared:

- **The probe metric:** `Foundry/Probe Healthy`, with no dimensions, is one metric for the whole account, so each deployment's "down" alarm would also react to another's probe.
- **The account budget:** each data stack would add one on the whole account's spend.
- **Reserved concurrency:** each deployment reserves Lambda concurrency from one account quota.
- **Leftovers:** destroying a deployment leaves its bucket and table behind on purpose. For personal deployments, that means orphans piling up in a shared account.

## Acceptance criteria

- [x] **N1. Named deployments.** `scripts/cloud.sh --name <name> <command>`, or `FOUNDRY_DEPLOYMENT=<name>`, runs every command against that deployment's own stacks, `Foundry-<name>-Data` and `Foundry-<name>-App`. A name is 1–24 characters: lowercase letters, digits and `-`, starting with a letter. Any other name is refused (exit 1) before AWS is called.
- [x] **N2. Main is bare.**
  - **No name** (or `--name main`) is the main deployment: `FoundryDataStack` and `FoundryAppStack`, exactly as before.
  - **Deploying main changes only its probe metric**, which gains a `Deployment=main` dimension, along with its alarm and its stacks' new `deployment=main` tag (N3). These are no-interruption updates: no resource is replaced and no deletion policy changes (checked by diffing main's templates before and after).
- [x] **N3. Nothing shared by accident.**
  - **Resources:** each deployment's resources are in its own stacks, with generated names.
  - **The probe:** each probe publishes `Foundry/Probe Healthy` with the dimension `Deployment=<name>`, and each deployment's alarm watches only its own.
  - **Tags:** stacks are tagged `deployment=<name>`.
  - **Tests:** synthesized templates for two names show no shared physical names, distinct metric dimensions, and their own tags.
- [x] **N4. Lighter personal deployments.**
  - **Concurrency:** a named deployment reserves none by default (`--concurrency` can still set some), so several fit in one account.
  - **Budget:** it creates no account budget unless `--budget` is passed.
  - **Main is unchanged:** its defaults stay 20 + 8 reserved, and its budget stays whatever it was given.
- [x] **N5. Full teardown for named deployments.**
  - **`destroy --data` on a named deployment** removes both stacks, including its bucket (every object version) and table. It asks for the deployment's name as confirmation, unless `--yes` is passed.
  - **On main,** `destroy --data` is refused.
  - **`destroy` without `--data`** keeps the data, as today.
  - **Retention policy:** a named data stack is created with a delete policy; main's data stays retained.
- [x] **N6. Seeing what's there.** `scripts/cloud.sh list` prints every deployment in the account and region: its name, URL, the build it serves, and whether its app stack exists.
- [x] **N7. Make and docs.**
  - **Make:** `make deploy|status|logs NAME=<name>` passes the name through.
  - **`docs/deploy.md`:** a "Personal deployments" section covers naming, defaults, teardown, and what stays shared (the CDK bootstrap, the account's quotas, the operator's saved CLI login).
  - **Elsewhere:** the README's AWS section mentions it, and the CHANGELOG lists it.
- [x] **N8. Live.** In the sandbox account:
  - deploy a named deployment, and see it serve at its own URL while main keeps serving its own build
  - see both probes' metrics with their own dimensions
  - `list` shows both
  - `destroy --data` removes the named one completely: no stacks, bucket or table left
  - main is redeployed with the dimension change, and its probe alarm returns to OK

## Non-goals

- A lock between two people deploying the same deployment at once. CloudFormation refuses a second update while one is in progress, and "one person deploys main" stays a team rule (in the docs).
- Separate accounts per participant, or per-name IAM boundaries: everyone in the account can still touch every stack.
- Migrating main's data into a named deployment, or the reverse.

## Assumptions and open questions

- **Assumption:** an S3 bucket with `RemovalPolicy.DESTROY` and `autoDeleteObjects` is emptied, every object version included, when its stack is deleted. The CDK docs state it for `autoDeleteObjects`. N8 proves it live, and if it's wrong, the teardown empties the bucket itself before deleting.
- **Assumption:** the CloudWatch alarm on the probe can filter by the new dimension, and main's alarm goes from missing data to OK once its probe publishes with it. The alarm treats missing data as breaching, so expect a short ALARM, and an alert email, right after main's redeploy. Every deploy runs the probe once after its checks pass, so the new metric has a data point at once. (Setting the alarm to OK alone wouldn't hold: the next evaluation would see missing data again.) **Found live (N8):** CloudFormation updated the alarm a minute before the probe function, so the alarm fired once (02:16) and returned to OK at 02:19, after the post-deploy probe run. It's a one-time cost of upgrading a main deployment, now in `docs/deploy.md`.

## Plan

1. **Infra:**
   - `infra/foundry_cloud/names.py` with `stack_names(name)` and validation.
   - `cloud_app.py` reads `-c deployment=<name>`.
   - `FoundryAppStack` and `FoundryDataStack` take a `deployment`: the probe gets an env var, the alarm a dimension, the stacks a tag, and named data stacks a delete policy with auto-delete on the bucket.
2. **Probe:** `lambda_probe.py` adds the `Deployment` dimension.
3. **`cloud.sh`:**
   - parse `--name` / `FOUNDRY_DEPLOYMENT` before the command, and validate it
   - set `DATA`/`APP` and pass `-c deployment` to every CDK call
   - named defaults for concurrency (0) and budget (none)
   - `list`; `destroy --data` (named only, confirmed); the probe run once after every deploy, so a new metric never alarms on missing data
   - `--name` taken anywhere on the line, and an empty one refused
4. **Tests:**
   - synthesized templates for main and two names (N1–N5)
   - name validation in Python and in `cloud.sh` (it refuses a bad name before any AWS call)
   - `destroy --data` refused on main, also before any AWS call
5. **Make and docs** (N7).
6. **Live** (N8), then a subagent Verify review.
