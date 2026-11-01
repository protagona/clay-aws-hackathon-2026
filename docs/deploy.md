# Deploying the eval sandbox to AWS

The app runs serverless in one AWS account, in **us-west-2**. It uses Lambda, S3, DynamoDB and Step Functions, with no VPC, servers or file systems. The design is in `docs/eval-sandbox/aws-deploy/`.

What you get:
- a public HTTPS URL (a Lambda function URL) serving the web app and the API, behind access tokens
- a command-line client anyone with a token can download from that URL
- a monthly spend cap ($30 by default) on model calls
- email alerts

## What it creates

| Stack | Holds | On `destroy` |
|---|---|---|
| `FoundryDataStack` | the state bucket (versioned, private, 35-day restore window), the DynamoDB table (35-day point-in-time recovery), the alerts topic, an optional account budget | **kept** |
| `FoundryAppStack` | the api, worker and probe functions (one container image), the function URL, the batch workflow, three alarms, 30-day logs | removed |

The legacy `FoundryStack` (`ClayFoundryEvalSandbox`, `infra/app.py`) is separate, and `cloud.sh` never touches it.

## First deploy into a fresh account (about 15 minutes)

1. **Tools on your machine:**
   - the AWS CLI v2
   - [uv](https://docs.astral.sh/uv/)
   - Node.js 20 or later
   - a running container engine: Docker, Finch or Podman (the image is built for arm64)
2. **Credentials for the target account,** in us-west-2. For example:
   ```sh
   export AWS_PROFILE=<profile>
   aws sts get-caller-identity
   ```
3. **Bedrock model access:** in the Bedrock console (us-west-2), enable the models that `config/bedrock*.toml` name. This is the one console step.
4. **Install and bootstrap, once per account:**
   ```sh
   (cd infra && npm ci)
   (cd infra && npx cdk bootstrap aws://<account>/us-west-2)
   ```
5. **Check, then deploy:**
   ```sh
   scripts/cloud.sh preflight
   scripts/cloud.sh deploy --email you@example.com                  # optional: --cap 50 --budget 100
   ```
   - The deploy prints the URL and the time it took.
   - **Confirm the subscription email** that AWS sends, or the alerts go nowhere.
   - Later deploys need no options; they reuse the email, cap and budget.
6. **Give people access:**
   ```sh
   scripts/cloud.sh token add ana         # prints the token once; send it privately
   scripts/cloud.sh token list
   scripts/cloud.sh token revoke ana      # stops working within 60 s
   ```
   People sign in at the URL with their token.
7. **Optional: bring your local runs:**
   ```sh
   scripts/cloud.sh seed                  # runs/ and tasks/ from this checkout
   ```
   - Seeding only works on an empty deployment.
   - If the deployment already holds a run, the seed is skipped as a whole.

### The command-line client

```sh
curl -O https://<id>.lambda-url.us-west-2.on.aws/cli/foundry-remote.pyz
python3 foundry-remote.pyz login https://<id>.lambda-url.us-west-2.on.aws/ <token>
python3 foundry-remote.pyz run start --prompt adb-001 --config bedrock --wait
python3 foundry-remote.pyz runs
```

- It needs Python 3.10 or later and nothing else.
- From a clone, `uv run foundry remote …` is the same program.
- `--json` gives the raw API output.
- Exit codes:
  - `0`: success
  - `1`: an API error, or a waited-for job that didn't succeed
  - `2`: a usage error, or not logged in
- `cloud.sh deploy` points the operator's own saved login at the new URL. The token stays.

## Day to day

| Command | What it does |
|---|---|
| `scripts/cloud.sh deploy` | Builds and ships the current checkout. See "How a deploy rolls out" below. |
| `scripts/cloud.sh status` | The URL, the build serving, the versions behind alias `live`, the cap. |
| `scripts/cloud.sh logs api\|worker\|probe\|batch [--since 2h] [--follow]` | CloudWatch logs (kept 30 days). |
| `scripts/cloud.sh stop` / `start` | The kill switch. While stopped, the API serves nothing (reserved concurrency 0), and the probe and its alarm are off. |
| `scripts/cloud.sh gc [--yes]` | Lists, then deletes, stored objects that no record refers to (leftovers of a lost write race). Deleted objects stay restorable for 35 days. |
| `scripts/cloud.sh destroy` | Removes the app stack. The data, tokens and alerts stay, and the next deploy reattaches them **at a new URL**: share the new URL. |

### How a deploy rolls out (O4)

1. A deploy refuses to start while jobs are in flight (`--force` overrides). In-flight work finishes on the version it started on either way.
2. The deploy updates the functions' unpublished code.
3. For the worker and then the API, it publishes a version and checks it:
   - the worker answers a health event
   - the API answers `/healthz` with the new build id, and `/api/session` with 401
4. Only then does it move alias `live`, which the URL and the workflow use.
5. A failed check stops the deploy. The previous version keeps serving, and `cloud.sh` says which version stayed live.
6. Rollback = check out the previous commit and deploy it.
7. A deploy with no change publishes nothing and changes no stack.

### Alerts (sent to the subscribed email)

- More than 10% of API requests fail (5xx) in 15 minutes.
- The public `/healthz` probe fails twice in 10 minutes.
- More than 5,000 API requests in 15 minutes (a cost guard).
- With `--budget`: the account's monthly cost passes 80%, actual or forecast.

### The spend cap

- Every run, scoring, batch and rubric draft reserves its worst-case model cost before it starts, and settles to the real cost when it ends.
- Past the cap, starts are refused (409) until the next UTC month. The refusal names the spend, the cap and the reset date.
- Set it with `deploy --cap <usd>`; `--cap 0` turns it off.

## Restoring data (O4)

The bucket keeps old versions of every object for 35 days. The table has point-in-time recovery for 35 days.

**A run's files were deleted** (for example by `gc --yes`, or by hand). Remove the delete markers, and the board shows the run again: its record still names those objects.
```sh
B=$(aws cloudformation describe-stacks --stack-name FoundryDataStack --query "Stacks[0].Outputs[?OutputKey=='Bucket'].OutputValue" --output text)
aws s3api list-object-versions --bucket "$B" --prefix "runs/<run id>/" \
  --query 'DeleteMarkers[?IsLatest].[Key,VersionId]' --output text |
while read -r key vid; do aws s3api delete-object --bucket "$B" --key "$key" --version-id "$vid"; done
```

**A record was lost or overwritten** (a `TREE` item: a run, batch or project).
1. Restore the table to a new table, at a time before the loss:
   ```sh
   T=$(aws cloudformation describe-stacks --stack-name FoundryDataStack --query "Stacks[0].Outputs[?OutputKey=='TableName'].OutputValue" --output text)
   aws dynamodb restore-table-to-point-in-time --source-table-name "$T" --target-table-name "$T-restore" \
     --restore-date-time 2026-11-01T12:00:00Z
   aws dynamodb wait table-exists --table-name "$T-restore"
   ```
2. Copy the item back, then delete the copy:
   ```sh
   aws dynamodb get-item --table-name "$T-restore" --key '{"pk":{"S":"TREE"},"sk":{"S":"run#<run id>"}}' \
     --query Item > item.json
   aws dynamodb put-item --table-name "$T" --item file://item.json
   aws dynamodb delete-table --table-name "$T-restore"
   ```
3. If the record names object versions that were since deleted, restore those as in the previous case.

## When something goes wrong

- **`AccessDenied` creating the function URL or its permissions:** in an AWS Organizations member account, a service control policy can forbid public function URLs (`lambda:FunctionUrlAuthType` = `NONE`). Ask the organization's admin for an exception for this account.
- **Model calls fail with `AccessDeniedException`:** enable the model in the Bedrock console, in us-west-2.
- **`Too many attempts; wait a minute.` at sign-in:** more than 10 sign-in attempts (or wrong header tokens) a minute from one address (or IPv6 /64) are refused until the minute is over.
- **The deploy says "N job(s) in flight":** wait (runs take at most 15 minutes), or pass `--force`.
- **A run shows "interrupted":** its worker stopped before finishing (a timeout or a crash). Its recorded steps are kept, and it can still be scored.
