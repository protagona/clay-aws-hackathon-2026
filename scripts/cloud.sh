#!/usr/bin/env bash
# The serverless deployment (aws-deploy): one command per operation, in the current credentials' account, us-west-2.
# Several deployments can share an account: --name <you> (or FOUNDRY_DEPLOYMENT) picks one, with its own stacks
# Foundry-<you>-Data and Foundry-<you>-App; no name is the main deployment, FoundryDataStack and FoundryAppStack. The
# legacy FoundryStack is never touched.
#
#   scripts/cloud.sh [--name <you>] <command>   # every command below works on that deployment (default: main)
#   scripts/cloud.sh list                       # every deployment in this account and region
#   scripts/cloud.sh preflight
#   scripts/cloud.sh deploy --email <alerts> [--budget <usd>|0] [--cap <usd>|0] [--concurrency <n>|0] [--force]
#                                                                        (later deploys reuse them)
#   scripts/cloud.sh status
#   scripts/cloud.sh token add <name> | token list | token revoke <name>
#   scripts/cloud.sh seed [--from <dir>]        # local runs/ and tasks/ into an empty deployment (spec S4)
#   scripts/cloud.sh gc [--yes]                 # objects no manifest references
#   scripts/cloud.sh reindex                    # rebuild stored board cards (after a new card field)
#   scripts/cloud.sh logs api|worker|probe|batch [--since 1h] [--follow]
#   scripts/cloud.sh stop | start               # the kill switch: no API requests are served while stopped
#   scripts/cloud.sh destroy [--yes]            # the app stack only; the data stays (spec D4)
#   scripts/cloud.sh --name <you> destroy --data [--yes]   # a named deployment, whole: its bucket and table too
#
# deploy (spec D1-D5, O4): builds the image (CDK_DOCKER: docker, finch or podman), deploys both stacks with the
# aliases held where they are, then publishes a worker and an api version and checks both (the worker's health
# event; the api's /healthz and /api/session); only when both pass does it move alias `live` on each. A failed
# check leaves both on the previous version. A deploy with no change publishes no version and changes no stack.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
REGION=us-west-2
export AWS_REGION=$REGION AWS_DEFAULT_REGION=$REGION

die() { echo "cloud.sh: $*" >&2; exit 1; }
say() { echo "▸ $*"; }

# The deployment (named-deployments N1, N2): checked before anything touches AWS, as infra/foundry_cloud/names.py does.
# --name is taken wherever it appears (a misplaced one must never fall back to main), and can't be empty.
DEPLOYMENT=${FOUNDRY_DEPLOYMENT:-}
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --name) [ $# -ge 2 ] && [ -n "$2" ] || die "--name needs a deployment name"; DEPLOYMENT=$2; shift 2 ;;
    --name=*) [ -n "${1#--name=}" ] || die "--name needs a deployment name"; DEPLOYMENT=${1#--name=}; shift ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
set -- ${ARGS[@]+"${ARGS[@]}"}
[ -n "$DEPLOYMENT" ] || DEPLOYMENT=main
if [ "$DEPLOYMENT" = main ]; then
  DATA=FoundryDataStack; APP=FoundryAppStack
else
  [[ "$DEPLOYMENT" =~ ^[a-z][a-z0-9-]{0,23}$ && "$DEPLOYMENT" != *- && "$DEPLOYMENT" != *--* ]] \
    || die "deployment name '$DEPLOYMENT': 1-24 lowercase letters, digits and single '-', starting with a letter"
  DATA="Foundry-$DEPLOYMENT-Data"; APP="Foundry-$DEPLOYMENT-App"
fi
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP:?}"' EXIT

out() {  # out <stack> <output key>: a stack output, or empty
  local v
  v=$(aws cloudformation describe-stacks --stack-name "$1" \
        --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" --output text 2>/dev/null) || v=""
  [ "$v" = "None" ] && v=""
  echo "$v"
}

stack_exists() { aws cloudformation describe-stacks --stack-name "$1" >/dev/null 2>&1; }

probe_fn() {  # the deployment's probe function
  aws cloudformation describe-stack-resources --stack-name $APP --query "StackResources[?ResourceType=='AWS::Lambda::Function' && starts_with(LogicalResourceId, 'Probe')].PhysicalResourceId | [0]" --output text
}

ops() {  # the table and bucket side (scripts/cloud_ops.py)
  local bucket table
  bucket=$(out $DATA Bucket); table=$(out $DATA TableName)
  [ -n "$bucket" ] && [ -n "$table" ] || die "no $DATA in account $(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo '(no credentials)'), $REGION (profile: ${AWS_PROFILE:-default}). Set AWS_PROFILE to the deployment's account, or deploy first."
  (cd "$REPO" && uv run --quiet python scripts/cloud_ops.py --bucket "$bucket" --table "$table" "$@")
}

engine() {
  if [ -n "${CDK_DOCKER:-}" ]; then echo "$CDK_DOCKER"; return; fi
  local e
  for e in docker finch podman; do
    if command -v "$e" >/dev/null 2>&1 && "$e" info >/dev/null 2>&1; then echo "$e"; return; fi
  done
  echo ""
}

unreserved() {
  aws lambda get-account-settings --query 'AccountLimit.UnreservedConcurrentExecutions' --output text
}

preflight() {
  local ok=1 e account
  command -v uv >/dev/null || { echo "✗ uv is not installed (https://docs.astral.sh/uv/)"; ok=0; }
  command -v npx >/dev/null || { echo "✗ Node.js (npx) is not installed"; ok=0; }
  [ -x "$REPO/infra/node_modules/.bin/cdk" ] || { echo "✗ the CDK CLI: run (cd infra && npm ci)"; ok=0; }
  e=$(engine)
  [ -n "$e" ] && echo "✓ container engine: $e" || { echo "✗ no running container engine (docker, finch or podman)"; ok=0; }
  if account=$(aws sts get-caller-identity --query Account --output text 2>/dev/null); then
    echo "✓ credentials: account $account, region $REGION"
    if stack_exists CDKToolkit; then echo "✓ CDK bootstrap"
    else echo "✗ not bootstrapped: (cd infra && npx cdk bootstrap aws://$account/$REGION)"; ok=0; fi
    echo "  Lambda unreserved concurrency: $(unreserved)"
  else
    echo "✗ no AWS credentials (aws sts get-caller-identity failed)"; ok=0
  fi
  [ $ok = 1 ] || die "preflight failed"
}

build_id() {
  local sha
  sha=$(git -C "$REPO" rev-parse --short HEAD)
  if [ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]; then
    echo "$sha-$(git -C "$REPO" diff HEAD | shasum | cut -c1-8)"
  else
    echo "$sha"
  fi
}

alias_version() {
  aws lambda get-alias --function-name "$1" --name live --query FunctionVersion --output text 2>/dev/null || echo '$LATEST'
}

cdk() {  # cdk <args...>: the deployment's CDK app (not infra/app.py, which holds the legacy stack)
  (cd "$REPO/infra" && CDK_DOCKER="$(engine)" ./node_modules/.bin/cdk --app "uv run python cloud_app.py" \
     --output cdk.out/cloud "$@")
}

event() {  # event <method> <path> <host>: a function URL (payload 2.0) event
  cat <<JSON
{"version": "2.0", "routeKey": "\$default", "rawPath": "$2", "rawQueryString": "", "isBase64Encoded": false,
 "headers": {"host": "$3"},
 "requestContext": {"domainName": "$3", "http": {"method": "$1", "path": "$2", "protocol": "HTTP/1.1",
                    "sourceIp": "127.0.0.1", "userAgent": "cloud.sh"}}}
JSON
}

invoke() {  # invoke <function> <version> <payload file> -> the response body in $TMP/resp.json; fails on a function error
  local err
  err=$(aws lambda invoke --function-name "$1" --qualifier "$2" --cli-binary-format raw-in-base64-out \
          --payload "fileb://$3" "$TMP/resp.json" --query FunctionError --output text) || return 1
  [ "$err" = "None" ] || { echo "  function error: $(head -c 400 "$TMP/resp.json")"; return 1; }
}

check_version() {  # check_version worker|api <function> <version> <build>
  local kind=$1 fn=$2 v=$3 build=$4 host
  if [ "$kind" = worker ]; then
    echo '{"kind": "healthz"}' > "$TMP/ev.json"
    invoke "$fn" "$v" "$TMP/ev.json" || return 1
    python3 - "$TMP/resp.json" "$build" <<'PY' || { echo "  the worker's health event failed: $(head -c 300 "$TMP/resp.json")"; return 1; }
import json, sys
r = json.load(open(sys.argv[1]))
sys.exit(0 if r.get("ok") is True and r.get("build") == sys.argv[2] else 1)
PY
    return 0
  fi
  host=$(out $APP Url | sed -e 's#^https://##' -e 's#/$##')
  event GET /healthz "$host" > "$TMP/ev.json"
  invoke "$fn" "$v" "$TMP/ev.json" || return 1
  python3 - "$TMP/resp.json" "ok $build" <<'PY' || { echo "  /healthz: $(head -c 300 "$TMP/resp.json")"; return 1; }
import json, sys
r = json.load(open(sys.argv[1]))
sys.exit(0 if r.get("statusCode") == 200 and r.get("body") == sys.argv[2] else 1)
PY
  event GET /api/session "$host" > "$TMP/ev.json"
  invoke "$fn" "$v" "$TMP/ev.json" || return 1
  python3 - "$TMP/resp.json" <<'PY' || { echo "  /api/session: $(head -c 300 "$TMP/resp.json")"; return 1; }
import json, sys
r = json.load(open(sys.argv[1]))  # the app booted and reached its store: sign-in is required
sys.exit(0 if r.get("statusCode") == 401 else 1)
PY
}

candidate() {  # candidate worker|api <function> <build>: publish a version and check it; echoes "<new> <current>"
  local kind=$1 fn=$2 build=$3 new cur
  aws lambda wait function-updated-v2 --function-name "$fn"
  new=$(aws lambda publish-version --function-name "$fn" --description "$build" --query Version --output text)
  cur=$(alias_version "$fn")
  if [ "$new" != "$cur" ]; then
    say "$kind: checking version $new" >&2
    check_version "$kind" "$fn" "$new" "$build" >&2 || {
      echo "  $kind version $new failed its check" >&2; echo "FAILED $cur"; return 0; }
  fi
  echo "$new $cur"
}

rollout() {  # rollout <worker fn> <api fn> <build>: both versions checked before either alias moves (O4)
  local worker=$1 api=$2 build=$3 w a
  w=$(candidate worker "$worker" "$build"); a=$(candidate api "$api" "$build")
  if [ "${w%% *}" = FAILED ] || [ "${a%% *}" = FAILED ]; then
    die "a new version failed its check; live stays on worker version ${w##* }, api version ${a##* } (the previous build keeps serving)"
  fi
  MOVED=0
  for pair in "worker $worker $w" "api $api $a"; do
    set -- $pair  # kind fn new cur
    if [ "$3" = "$4" ]; then say "$1: unchanged (version $4)"; continue; fi
    aws lambda update-alias --function-name "$2" --name live --function-version "$3" >/dev/null
    say "$1: live → version $3"
    MOVED=1
  done
}

concurrency() {  # concurrency <api fn> <worker fn> <limit>: spec A6's function-level limit (0 = none), worker 8
  local api=$1 worker=$2 limit=$3 have free
  have=$(aws lambda get-function-concurrency --function-name "$api" --query ReservedConcurrentExecutions --output text 2>/dev/null || echo None)
  if [ "$have" = 0 ]; then echo "  note: the API is stopped (reserved concurrency 0); run: scripts/cloud.sh start"; return; fi
  if [ "$limit" = 0 ]; then
    [ "$have" = None ] || aws lambda delete-function-concurrency --function-name "$api"
    say "no concurrency limit (--concurrency 0)"; return
  fi
  [ "$have" = "$limit" ] && return 0
  free=$(unreserved)
  [ "$have" = None ] || free=$(( free + have ))  # this function's own reservation is freed by changing it
  if [ "$free" -ge $(( 100 + limit + 8 )) ]; then  # Lambda keeps 100 unreserved for the rest of the account
    aws lambda put-function-concurrency --function-name "$api" --reserved-concurrent-executions "$limit" >/dev/null
    aws lambda put-function-concurrency --function-name "$worker" --reserved-concurrent-executions 8 >/dev/null
    say "reserved concurrency: api $limit, worker 8 (spec A6)"
  else
    echo "  WARNING: this account has $free unreserved concurrency, too little to reserve api $limit + worker 8 and keep"
    echo "  the 100 Lambda requires; spec A6's limit is NOT set. Ask for a Lambda concurrency quota increase, then deploy again."
  fi
}

cmd_deploy() {
  local email="" budget="" cap="" limit="" force=0 t0 build av=\$LATEST wv=\$LATEST api worker url bucket table n
  while [ $# -gt 0 ]; do
    case "$1" in
      --email) email=$2; shift 2 ;;
      --budget) budget=$2; shift 2 ;;
      --cap) cap=$2; shift 2 ;;
      --concurrency) limit=$2; shift 2 ;;
      --force) force=1; shift ;;
      *) die "deploy: unknown option $1" ;;
    esac
  done
  t0=$(date +%s)
  [ -n "$email" ] || email=$(out $DATA AlertEmail)
  [ -n "$email" ] || die "deploy: --email <address> is required the first time (alerts go there)"
  [ -n "$budget" ] || budget=$(out $DATA BudgetUsd)
  [ "$budget" = 0 ] && budget=""
  [ -n "$cap" ] || cap=$(out $DATA CapUsd)
  [ -n "$cap" ] || cap=30
  [ -n "$limit" ] || limit=$(out $DATA ApiConcurrency)
  [ -n "$limit" ] || { [ "$DEPLOYMENT" = main ] && limit=20 || limit=0; }  # named: none reserved (N4)
  preflight
  build=$(build_id)
  if stack_exists $APP; then
    api=$(out $APP ApiFunction); worker=$(out $APP WorkerFunction)
    av=$(alias_version "$api"); wv=$(alias_version "$worker")
    n=$(ops live-jobs)
    if [ "$n" != 0 ] && [ $force = 0 ]; then
      die "$n job(s) in flight; wait for them, or pass --force (in-flight work finishes on its version)"
    fi
  fi
  say "deploying $DEPLOYMENT: build $build (cap \$$cap${budget:+, budget \$$budget})"
  cdk deploy $DATA $APP --require-approval never -c "deployment=$DEPLOYMENT" -c "email=$email" -c "build=$build" -c "cap=$cap" -c "concurrency=$limit" \
    ${budget:+-c "budget=$budget"} --parameters "$APP:ApiVersion=$av" --parameters "$APP:WorkerVersion=$wv"
  api=$(out $APP ApiFunction); worker=$(out $APP WorkerFunction); url=$(out $APP Url)
  ops session-key
  rollout "$worker" "$api" "$build"
  if [ $MOVED = 1 ]; then  # the stack learns where live points, so the next deploy with no change changes nothing
    aws cloudformation update-stack --stack-name $APP --use-previous-template --capabilities CAPABILITY_IAM \
      --parameters "ParameterKey=ApiVersion,ParameterValue=$(alias_version "$api")" \
                   "ParameterKey=WorkerVersion,ParameterValue=$(alias_version "$worker")" \
                   "ParameterKey=BootstrapVersion,UsePreviousValue=true" >/dev/null
    aws cloudformation wait stack-update-complete --stack-name $APP
  fi
  concurrency "$api" "$worker" "$limit"
  ops remote-json "$url" >/dev/null || true
  for _ in $(seq 1 30); do
    [ "$(curl -s --max-time 10 "${url%/}/healthz" || true)" = "ok $build" ] && break
    sleep 2
  done
  [ "$(curl -s --max-time 10 "${url%/}/healthz" || true)" = "ok $build" ] || die "$url/healthz doesn't answer build $build"
  # Run the probe once now, so its metric has a real data point at once and the alarm settles on the next evaluation
  # rather than after the probe's first scheduled run. (CloudFormation may update the alarm before the probe, so an
  # alarm switched to a new metric can still fire once: named-deployments N8.)
  aws lambda invoke --function-name "$(probe_fn)" --cli-binary-format raw-in-base64-out --payload '{}' \
    "$TMP/probe.json" >/dev/null 2>&1 || echo "  note: couldn't run the probe now; it runs every 5 minutes"
  echo
  say "$DEPLOYMENT: serving build $build at $url ($(( $(date +%s) - t0 )) s)"
  echo "  first time? add a token: scripts/cloud.sh token add <your name>; then sign in at the URL"
  echo "  CLI: curl -O ${url%/}/cli/foundry-remote.pyz && python3 foundry-remote.pyz login $url <token>"
}

cmd_status() {
  local api worker
  stack_exists $APP || die "no $APP in this account/region"
  api=$(out $APP ApiFunction); worker=$(out $APP WorkerFunction)
  echo "deployment: $DEPLOYMENT ($DATA, $APP)"
  echo "URL:     $(out $APP Url)"
  echo "build:   $(curl -s --max-time 10 "$(out $APP Url)healthz" || echo '(no answer)')"
  echo "api:     live → version $(alias_version "$api"), reserved concurrency $(aws lambda get-function-concurrency --function-name "$api" --query ReservedConcurrentExecutions --output text 2>/dev/null)"
  echo "worker:  live → version $(alias_version "$worker")"
  echo "cap:     \$$(out $APP Cap) a month · alerts to $(out $DATA AlertEmail)"
}

cmd_logs() {
  local which=${1:-api} since=1h follow="" group fn
  shift || true
  while [ $# -gt 0 ]; do
    case "$1" in --since) since=$2; shift 2 ;; --follow) follow=--follow; shift ;; *) die "logs: unknown option $1" ;; esac
  done
  case "$which" in
    api) fn=$(out $APP ApiFunction) ;; worker) fn=$(out $APP WorkerFunction) ;;
    probe) fn=$(probe_fn) ;;
    batch) group=$(out $APP BatchLogGroup) ;;
    *) die "logs: api, worker, probe or batch" ;;
  esac
  [ -n "${group:-}" ] || group=$(aws lambda get-function-configuration --function-name "$fn" --query LoggingConfig.LogGroup --output text)
  aws logs tail "$group" --since "$since" $follow
}

cmd_stop() {
  local api
  api=$(out $APP ApiFunction)
  aws lambda put-function-concurrency --function-name "$api" --reserved-concurrent-executions 0 >/dev/null
  aws events disable-rule --name "$(out $APP ProbeRule)"
  aws cloudwatch disable-alarm-actions --alarm-names "$(out $APP ProbeAlarm)"
  say "stopped: the URL answers 429 to every request; the probe and its alarm are off (scripts/cloud.sh start)"
}

cmd_start() {
  local api worker limit
  api=$(out $APP ApiFunction); worker=$(out $APP WorkerFunction)
  aws lambda delete-function-concurrency --function-name "$api"
  limit=$(out $DATA ApiConcurrency); concurrency "$api" "$worker" "${limit:-20}"
  aws cloudwatch set-alarm-state --alarm-name "$(out $APP ProbeAlarm)" --state-value OK --state-reason "cloud.sh start"
  aws cloudwatch enable-alarm-actions --alarm-names "$(out $APP ProbeAlarm)"
  aws events enable-rule --name "$(out $APP ProbeRule)"
  say "started: serving at $(out $APP Url)"
}

cmd_destroy() {
  local yes=0 data=0 email
  for a in "$@"; do
    case "$a" in --yes) yes=1 ;; --data) data=1 ;; *) die "destroy: unknown option $a" ;; esac
  done
  if [ $data = 1 ] && [ "$DEPLOYMENT" = main ]; then  # before any AWS call (named-deployments N5)
    die "destroy --data: refused for the main deployment, whose data is kept on purpose; name a personal one with --name"
  fi
  if [ $data = 1 ]; then
    stack_exists $DATA || stack_exists $APP || die "no deployment named $DEPLOYMENT here"
    if [ $yes = 0 ]; then
      read -r -p "Destroy deployment $DEPLOYMENT completely: $APP and $DATA, with its runs, tokens and bucket? Type its name: " answer
      [ "$answer" = "$DEPLOYMENT" ] || die "not destroyed"
    fi
    email=$(out $DATA AlertEmail)
    stack_exists $APP && cdk destroy $APP --force -c "deployment=$DEPLOYMENT" -c "email=${email:-x@example.com}"
    stack_exists $DATA && cdk destroy $DATA --force -c "deployment=$DEPLOYMENT" -c "email=${email:-x@example.com}"
    # Lambda recreates a function's log group when it logs during the stack's deletion (CDK's bucket-emptying
    # function does): delete what's left. The capitalised -Data-/-App- can't match another (lowercase) name.
    for g in $(aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/$DATA-" --query 'logGroups[].logGroupName' --output text) \
             $(aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/$APP-" --query 'logGroups[].logGroupName' --output text); do
      [ "$g" = None ] || aws logs delete-log-group --log-group-name "$g"
    done
    say "destroyed deployment $DEPLOYMENT: $APP, $DATA, its bucket (every object version), its table and its logs"
    return
  fi
  stack_exists $APP || die "no $APP to destroy"
  if [ $yes = 0 ]; then
    read -r -p "Destroy $APP (functions, URL, workflow, alarms)? The data stays. Type the stack name: " answer
    [ "$answer" = "$APP" ] || die "not destroyed"
  fi
  email=$(out $DATA AlertEmail)
  cdk destroy $APP --force -c "deployment=$DEPLOYMENT" -c "email=$email"
  say "destroyed $APP; $DATA (bucket, table, tokens, alerts) is kept. A deploy reattaches it at a new URL."
}

cmd_list() {  # every deployment in this account and region (named-deployments N6)
  local stacks name app url build
  stacks=$(aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE UPDATE_ROLLBACK_COMPLETE \
             CREATE_IN_PROGRESS UPDATE_IN_PROGRESS UPDATE_COMPLETE_CLEANUP_IN_PROGRESS UPDATE_ROLLBACK_IN_PROGRESS \
             UPDATE_ROLLBACK_FAILED ROLLBACK_COMPLETE DELETE_IN_PROGRESS DELETE_FAILED IMPORT_COMPLETE \
             --query "StackSummaries[?StackName=='FoundryDataStack' || (starts_with(StackName, 'Foundry-') && ends_with(StackName, '-Data'))].StackName" \
             --output text | tr '\t' '\n' | sort)
  [ -n "$stacks" ] || { echo "No deployments in this account ($REGION)."; return; }
  printf "%-26s %-72s %s\n" DEPLOYMENT URL BUILD
  for s in $stacks; do
    if [ "$s" = FoundryDataStack ]; then name=main; app=FoundryAppStack; else name=${s#Foundry-}; name=${name%-Data}; app="Foundry-$name-App"; fi
    if stack_exists "$app"; then
      url=$(out "$app" Url); build=$(curl -s --max-time 10 "${url%/}/healthz" | sed 's/^ok //' || true)
    else
      url="(app stack destroyed; data kept)"; build=""
    fi
    printf "%-26s %-72s %s\n" "$name" "$url" "${build:--}"
  done
}

case "${1:-}" in
  preflight) preflight ;;
  list) cmd_list ;;
  deploy) shift; cmd_deploy "$@" ;;
  status) cmd_status ;;
  token)
    case "${2:-}" in
      add) [ -n "${3:-}" ] || die "token add <name>"; ops token-add "$3" ;;
      list) ops token-list ;;
      revoke) [ -n "${3:-}" ] || die "token revoke <name>"; ops token-revoke "$3" ;;
      *) die "token add <name> | list | revoke <name>" ;;
    esac ;;
  seed) shift; ops seed "$@" ;;
  gc) shift; ops gc "$@" ;;
  reindex) ops reindex "$(out $APP WorkerFunction)" ;;
  logs) shift; cmd_logs "$@" ;;
  stop) cmd_stop ;;
  start) cmd_start ;;
  destroy) shift; cmd_destroy "$@" ;;
  *) awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"; exit 2 ;;
esac
