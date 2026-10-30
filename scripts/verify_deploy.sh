#!/usr/bin/env bash
# Live checks after `cdk deploy` (A-1, C-3, D-8 / spec L4), as the deployed least-privilege HarnessRole:
#   1. a live run with --evaluate (Bedrock agent, judge, summarizer) mirrors the run and its report to S3
#   2. the run's files are listed under s3://<RunsBucket>/runs/<run id>/
#   3. the role can read samples/ (if scripts/stage_samples.sh ran) and cannot write outside runs/
#
#   AWS_PROFILE=<profile> scripts/verify_deploy.sh [prompt id, default adb-001] [config, default config/bedrock.toml]
#
# Costs one live run (about $0.20 with the default config). Your profile needs sts:AssumeRole on the role.
set -euo pipefail

STACK=ClayFoundryEvalSandbox
REGION=${AWS_REGION:-us-west-2}
PROMPT=${1:-adb-001}
CONFIG=${2:-config/bedrock.toml}

output() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
BUCKET=$(output RunsBucketName)
ROLE=$(output HarnessRoleArn)
[ -n "$BUCKET" ] && [ "$BUCKET" != "None" ] || { echo "Stack $STACK has no outputs; deploy first" >&2; exit 2; }
echo "bucket $BUCKET"
echo "role   $ROLE"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
sed -E "s|^s3_uri = \"[^\"]*\"|s3_uri = \"s3://$BUCKET\"|" "$CONFIG" > "$TMP/config.toml"
grep -q "^s3_uri = \"s3://$BUCKET\"" "$TMP/config.toml" || { echo "No [storage] s3_uri line in $CONFIG" >&2; exit 2; }

# From here on, only the role's credentials: proves the role alone is enough.
CREDS=$(aws sts assume-role --role-arn "$ROLE" --role-session-name foundry-verify --region "$REGION" \
  --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' --output text)
read -r AK SK ST <<<"$CREDS"
unset AWS_PROFILE AWS_DEFAULT_PROFILE
export AWS_ACCESS_KEY_ID=$AK AWS_SECRET_ACCESS_KEY=$SK AWS_SESSION_TOKEN=$ST AWS_REGION=$REGION
aws sts get-caller-identity --query Arn --output text

set +e
OUT=$(uv run foundry run --config "$TMP/config.toml" --prompt "$PROMPT" --evaluate)
RC=$?
set -e
echo "$OUT"
RUN_ID=$(basename "$(echo "$OUT" | head -1)")
[ -n "$RUN_ID" ] || { echo "foundry run printed no run directory (exit $RC)" >&2; exit 1; }

echo "--- s3://$BUCKET/runs/$RUN_ID/"
LISTING=$(aws s3 ls "s3://$BUCKET/runs/$RUN_ID/")
echo "$LISTING"
for f in run.json trace.jsonl scores.json report.md rubric.json; do
  echo "$LISTING" | grep -q " $f\$" || { echo "FAIL: $f not mirrored" >&2; exit 1; }
done

echo "--- samples/ (read-only)"
aws s3 ls "s3://$BUCKET/samples/" || echo "(no samples staged; run scripts/stage_samples.sh)"
if echo probe | aws s3 cp - "s3://$BUCKET/not-runs/probe.txt" 2>/dev/null; then
  echo "FAIL: the role could write outside runs/" >&2; exit 1
fi
echo "role cannot write outside runs/: ok"
echo "PASS (run exit $RC)"
