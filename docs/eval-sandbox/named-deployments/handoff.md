# Personal deployments in a shared account — Handoff

**Spec:** ./spec.md   **Date:** 2026-11-02   **Track:** Quick   **Build:** `b5d1734`, live on main in the sandbox (later commits change docs and `destroy --data`'s log cleanup)

**What shipped:** `scripts/cloud.sh --name <you>` (or `FOUNDRY_DEPLOYMENT`, or `make … NAME=<you>`) gives each participant their own deployment, `Foundry-<you>-Data` and `Foundry-<you>-App`, in the shared account. No name is the main deployment, with its stacks and data as before. `cloud.sh list` shows every deployment, and `destroy --data` removes a personal one completely.

## Verification

- [x] **N1, named deployments:**
  - **Name rules:** `infra/foundry_cloud/names.py` and the same check in `cloud.sh`, run before any AWS call. Tested by `test_deployment_names`, including a trailing newline.
  - **Refusals before AWS:** `test_cloud_sh_refuses_a_bad_name_and_main_data_teardown_before_calling_aws` uses a stub `aws` that records every call.
  - **`--name` is taken anywhere and never empty:** `test_cloud_sh_takes_the_name_anywhere_and_never_falls_back_to_main`.
  - **The deployment reaches every `cdk` call:** `test_cloud_sh_passes_the_deployment_to_every_cdk_call`.
  - **The CDK app names its stacks from `-c deployment`, and refuses a bad name:** `test_the_cdk_app_names_its_stacks_from_the_deployment_context`.
- [x] **N2, main is bare:**
  - **Tests:** `test_main_keeps_its_names_and_its_data` (still Retain).
  - **Live:** main kept its bucket and table names, with no resource deleted or replaced. Its stacks gained `deployment=main` (`evidence/live-main.txt`).
- [x] **N3, nothing shared:**
  - **Tests:** `test_two_named_deployments_share_no_name_and_watch_their_own_probe` checks no fixed physical names, the `deployment` tag on every taggable resource, and each alarm on its own dimension. `test_the_probe_reports_its_deployment`.
  - **Live:** the metrics `Healthy Deployment=trial` and `Deployment=main`, with each alarm watching its own.
- [x] **N4, lighter defaults:** `test_a_named_deployment_is_removed_whole` (no budget, concurrency 0). Live: trial reported "reserved concurrency None", while main kept 20.
- [x] **N5, full teardown:**
  - **Live:** `destroy --data --yes` removed both stacks, the bucket (404) and the table (not found). No resources tagged `deployment=trial` remain (`evidence/live-teardown.txt`).
  - **Main refuses `--data`:** tested before any AWS call.
- [x] **N6, `list`:** live, it showed trial and main with their URLs and builds, and main alone after teardown.
- [x] **N7, Make and docs:**
  - **Make:** `NAME=` on `deploy`, `status` and `logs`, plus `make deployments`.
  - **Docs:** `docs/deploy.md` "Personal deployments", the README and the CHANGELOG.
- [x] **N8, live** (2026-11-02, account 283171504459):
  - **Trial:** `--name trial deploy` served `b5d1734` at its own URL, while main still served `14e8c38` (`evidence/deploy-trial.log`, `live-both.txt`).
  - **Main:** redeployed, now serving `b5d1734` (`deploy-main.log`). Its probe alarm went ALARM at 02:16 and back to OK at 02:19 (see Decisions).
  - **Teardown:** trial fully removed.

**Suites:**
- **pytest:** 598 passed, 12 skipped, 4 network tests deselected.
- **Infra tests:** 24 passed.
- **ruff and gitleaks:** clean.

**Gate history:**
- **Define:** self-review.
- **Verify:** one subagent pass, which found 1 blocker. A `--name` after the command was ignored, so `stop`, `start`, `reindex` and `token …` acted on main, and `--name ""` meant main too.
  - **Fix:** `--name` is now taken anywhere on the line, and an empty one is refused. The fix is tested, and the test fails on the old code.
- **Verify's minor findings, all fixed:**
  - the help text was cut short
  - the Python name check accepted a trailing newline
  - `list` missed stacks mid-deploy or with a failed teardown
  - spec N2 didn't mention main's tags
  - test gaps: the CDK app's context, `-c deployment` on `cdk` calls, and the tag on every resource
  - the alarm after main's redeploy (see Decisions)

**Decisions and deviations:**
- **Main's one-time alarm.** Upgrading main switched its probe alarm to a new metric, and CloudFormation updated the alarm a minute before the probe function. So the alarm fired once and sent one email, and cleared 3 minutes later.
  - The "set the alarm to OK" idea from the spec wouldn't have held. Every deploy now runs the probe once at the end instead, which makes the alarm settle on its next evaluation.
  - This is documented in `docs/deploy.md` for whoever upgrades main in another account.
- **A leftover log group, found live.** After `destroy --data`, the log group of CDK's bucket-emptying function was left behind: it logged during the deletion, so Lambda recreated the group.
  - **Fix:** `destroy --data` now deletes the deployment's leftover `/aws/lambda/Foundry-<name>-Data-*` and `-App-*` groups. I deleted the trial's leftover by hand.
  - **Not yet run live:** the new loop itself. The next `destroy --data` exercises it.
- **Named log groups.** A named `destroy` without `--data` deletes the app's log groups with the stack, while main keeps its own 30 days. Personal deployments are disposable.
- **Podman machine.** The podman VM no longer existed (removed while freeing disk), so I created a new default machine for the image build.

**Follow-ups:**
- No lock between two people deploying the same deployment (a non-goal). "One person deploys main" is a team rule in the docs.
- The old dimensionless `Foundry/Probe Healthy` metric gets no more data and expires from CloudWatch by itself (15 months, unbilled once idle).
- `NAME` in the Makefile can be inherited from the environment. A bad value is refused safely.
- CI lint: added the same day, a `lint` job running `make lint`.

**Risks to watch:**
- Every participant can still touch every stack in the account (no per-name IAM, a non-goal).
- Several personal deployments share the account's Lambda concurrency: with none reserved, a busy main can starve them, and the reverse.
