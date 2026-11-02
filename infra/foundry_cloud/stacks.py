"""The serverless deployment (aws-deploy design ADR-12): a data stack that outlives every deploy, and an app stack
that `cloud.sh destroy` can remove and a redeploy reattaches (spec D4).

FoundryDataStack: the state bucket, the table, the alerts topic and the optional account budget. All RETAIN, and
nothing in it refers to the app stack.

FoundryAppStack: one image (Dockerfile.lambda) behind three functions (api, worker, probe), the api's public
function URL, the batch workflow, the probe schedule, alarms and 30-day log groups. No VPC, subnets or file systems
(spec X1).

**Safe rollout (O4).** The api's URL, the api's worker invokes and the workflow all go through each function's
alias `live`. The alias's version is a stack parameter (`ApiVersion`, `WorkerVersion`; `$LATEST` on the first
deploy). `cloud.sh deploy` passes the alias's current version, so a deploy only updates `$LATEST`. It then publishes
a version, checks it, and only then moves `live` to it. A failed check leaves `live` where it was.
"""

from __future__ import annotations

from pathlib import Path

from foundry_cloud.names import MAIN
from aws_cdk import CfnOutput, CfnParameter, Duration, RemovalPolicy, Stack, Tags
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from aws_cdk import aws_stepfunctions as sfn
from constructs import Construct

from foundry_cloud.batch_sm import batch_state_machine
from stack import BEDROCK_ACTIONS, PROFILE_REGIONS, bedrock_models

REPO = Path(__file__).resolve().parents[2]
# Partition keys a worker may touch: never TOKEN, CONFIG or RATE (the token hashes and the session key; ADR-12).
WORKER_KEYS = ["TREE", "JOB", "LOCK", "SLOT", "SPEND", "UPLOAD"]
TABLE_ITEM_ACTIONS = ["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query", "dynamodb:PutItem",
                      "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:ConditionCheckItem"]


def _tag(stack: Stack, deployment: str) -> None:
    Tags.of(stack).add("project", "clay-foundry")
    Tags.of(stack).add("owner", "team-46")
    Tags.of(stack).add("deployment", deployment)


class FoundryDataStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, email: str, budget_usd: float | None = None,
                 cap_usd: float | None = 30.0, api_concurrency: int = 20, deployment: str = MAIN, **kw) -> None:
        super().__init__(scope, cid, **kw)
        # Main's data outlives its stacks (spec D4); a named (personal) deployment is removed whole by
        # `cloud.sh destroy --data` (named-deployments N5), bucket versions included.
        keep = deployment == MAIN
        policy = RemovalPolicy.RETAIN if keep else RemovalPolicy.DESTROY
        self.bucket = s3.Bucket(
            self, "State", versioned=True, block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True,
            encryption=s3.BucketEncryption.S3_MANAGED, removal_policy=policy, auto_delete_objects=not keep,
            # The browser POSTs uploads straight here (presigned, ADR-10); nothing else is allowed cross-origin.
            cors=[s3.CorsRule(allowed_methods=[s3.HttpMethods.POST], allowed_origins=["*"], allowed_headers=["*"])],
            lifecycle_rules=[
                s3.LifecycleRule(id="uploads", prefix="uploads/", expiration=Duration.days(1)),
                s3.LifecycleRule(id="restore-window", noncurrent_version_expiration=Duration.days(35),
                                 abort_incomplete_multipart_upload_after=Duration.days(1)),
                s3.LifecycleRule(id="delete-markers", expired_object_delete_marker=True),
            ])
        self.table = ddb.Table(
            self, "Table", partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST, time_to_live_attribute="ttl",
            point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True, recovery_period_in_days=35),
            removal_policy=policy)
        self.topic = sns.Topic(self, "Alerts", display_name="Foundry alerts")
        self.topic.apply_removal_policy(policy)
        self.topic.add_subscription(subs.EmailSubscription(email))
        if budget_usd:  # account-wide: tag filters don't work in an organization member account (spec O2)
            budgets.CfnBudget(self, "Budget", budget=budgets.CfnBudget.BudgetDataProperty(
                budget_type="COST", time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=budget_usd, unit="USD")),
                notifications_with_subscribers=[budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN", notification_type=kind, threshold=80,
                        threshold_type="PERCENTAGE"),
                    subscribers=[budgets.CfnBudget.SubscriberProperty(address=email, subscription_type="EMAIL")])
                    for kind in ("ACTUAL", "FORECASTED")])
        CfnOutput(self, "Bucket", value=self.bucket.bucket_name)
        CfnOutput(self, "TableName", value=self.table.table_name)
        CfnOutput(self, "AlertEmail", value=email)
        CfnOutput(self, "BudgetUsd", value=f"{budget_usd:g}" if budget_usd else "0")
        # The cap is the app stack's setting, kept here too so a deploy after `destroy` keeps it ("0" = no cap).
        CfnOutput(self, "CapUsd", value="0" if cap_usd is None else f"{cap_usd:g}")
        # Spec A6's function-level limit, applied by cloud.sh (it checks the account can spare it) and kept here.
        CfnOutput(self, "ApiConcurrency", value=str(api_concurrency))
        CfnOutput(self, "Deployment", value=deployment)
        _tag(self, deployment)


class FoundryAppStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, data: FoundryDataStack, build: str = "dev",
                 cap_usd: float | None = 30.0, deployment: str = MAIN, **kw) -> None:
        super().__init__(scope, cid, **kw)
        bucket, table, topic = data.bucket, data.table, data.topic
        api_version = CfnParameter(self, "ApiVersion", default="$LATEST",
                                   description="The version alias live points at (cloud.sh sets it)")
        worker_version = CfnParameter(self, "WorkerVersion", default="$LATEST",
                                      description="The version alias live points at (cloud.sh sets it)")

        def image(cmd: str) -> lam.DockerImageCode:
            return lam.DockerImageCode.from_image_asset(str(REPO), file="Dockerfile.lambda", cmd=[cmd],
                                                        platform=ecr_assets.Platform.LINUX_ARM64)

        def log_group(name: str) -> logs.LogGroup:
            return logs.LogGroup(self, f"{name}Logs", retention=logs.RetentionDays.ONE_MONTH,  # main's are kept 30 days
                                 removal_policy=RemovalPolicy.RETAIN if deployment == MAIN else RemovalPolicy.DESTROY)

        env = {"STATE_BUCKET": bucket.bucket_name, "TABLE": table.table_name, "FOUNDRY_BUILD": build}

        # --- worker: runs, scorings, batch items (spec S3: one invocation, at most 15 minutes) ---------------------
        worker = lam.DockerImageFunction(
            self, "Worker", code=image("foundry.lambda_worker.handler"), architecture=lam.Architecture.ARM_64,
            memory_size=2048, ephemeral_storage_size=_mib(2048), timeout=Duration.seconds(900), environment=env,
            retry_attempts=0, max_event_age=Duration.seconds(900), log_group=log_group("Worker"))
        worker_live = lam.Alias(self, "WorkerLive", alias_name="live", version=lam.Version.from_version_attributes(
            self, "WorkerVersionRef", version=worker_version.value_as_string, lambda_=worker))
        profile_ids, regional_ids = bedrock_models()
        profiles = [f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/{p}" for p in profile_ids]

        def bedrock(fn: lam.IFunction) -> None:  # the configs' models only (the existing bedrock_models() rules)
            fn.add_to_role_policy(iam.PolicyStatement(actions=BEDROCK_ACTIONS, resources=profiles + [
                f"arn:aws:bedrock:{self.region}::foundation-model/{m}" for m in regional_ids]))
            fn.add_to_role_policy(iam.PolicyStatement(actions=BEDROCK_ACTIONS, resources=[
                f"arn:aws:bedrock:{r}::foundation-model/{p.removeprefix('us.')}" for p in profile_ids for r in PROFILE_REGIONS],
                conditions={"StringLike": {"bedrock:InferenceProfileArn": profiles}}))

        bedrock(worker)
        bucket.grant_read_write(worker)  # includes ListBucket, so a missing key is a 404, not a 403
        bucket.grant_delete(worker)
        worker.add_to_role_policy(iam.PolicyStatement(actions=TABLE_ITEM_ACTIONS, resources=[table.table_arn],
                                                      conditions={"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": WORKER_KEYS}}))

        # --- the batch workflow (ADR-7) ------------------------------------------------------------------------
        machine_logs = log_group("Batch")
        machine = batch_state_machine(self, worker_live, logs=sfn.LogOptions(destination=machine_logs,
                                                                              level=sfn.LogLevel.ERROR))

        # --- api: the web app and the API behind a public function URL (ADR-3) ------------------------------------
        api = lam.DockerImageFunction(
            self, "Api", code=image("foundry.lambda_api.handler"), architecture=lam.Architecture.ARM_64,
            memory_size=2048, ephemeral_storage_size=_mib(2048), timeout=Duration.seconds(300),
            environment={**env, "WORKER_FUNCTION": worker_live.function_arn, "STATE_MACHINE": machine.state_machine_arn,
                         "MONTHLY_CAP_USD": "" if cap_usd is None else f"{cap_usd:g}"},
            log_group=log_group("Api"))
        api_live = lam.Alias(self, "ApiLive", alias_name="live", version=lam.Version.from_version_attributes(
            self, "ApiVersionRef", version=api_version.value_as_string, lambda_=api))
        url = api_live.add_function_url(auth_type=lam.FunctionUrlAuthType.NONE)
        bedrock(api)  # rubric drafts call the judge model inside the request (design notes, slice 6b)
        table.grant_read_write_data(api)
        bucket.grant_read_write(api)
        bucket.grant_delete(api)
        worker_live.grant_invoke(api)
        machine.grant_start_execution(api)
        machine.grant_read(api)
        machine.grant_execution(api, "states:StopExecution")

        # --- probe and alarms (spec O2, ADR-12) ------------------------------------------------------------------
        probe = lam.DockerImageFunction(
            self, "Probe", code=image("foundry.lambda_probe.handler"), architecture=lam.Architecture.ARM_64,
            memory_size=256, timeout=Duration.seconds(30), environment={"PROBE_URL": url.url, "DEPLOYMENT": deployment},
            log_group=log_group("Probe"))
        probe.add_to_role_policy(iam.PolicyStatement(actions=["cloudwatch:PutMetricData"], resources=["*"],
                                                     conditions={"StringEquals": {"cloudwatch:namespace": "Foundry/Probe"}}))
        rule = events.Rule(self, "ProbeSchedule", schedule=events.Schedule.rate(Duration.minutes(5)),
                           targets=[targets.LambdaFunction(probe, retry_attempts=0)])
        alarm = cw_actions.SnsAction(topic)
        # Function URL metrics carry FunctionName and Resource = "<function>:<alias>" for an alias's URL
        # (lambda/latest/dg/urls-monitoring.html); checked live with list-metrics at Verify.
        live = {"FunctionName": api.function_name, "Resource": f"{api.function_name}:live"}

        def url_metric(name: str) -> cw.Metric:
            return cw.Metric(namespace="AWS/Lambda", metric_name=name, dimensions_map=live, statistic="Sum",
                             period=Duration.minutes(15))

        errors = cw.Alarm(self, "ApiErrorRate", alarm_description="Over 10% of API requests failed (5xx) in 15 minutes",
                          metric=cw.MathExpression(expression="IF(req > 0, 100 * e5 / req, 0)",
                                                   using_metrics={"e5": url_metric("Url5xxCount"), "req": url_metric("UrlRequestCount")},
                                                   period=Duration.minutes(15)),
                          threshold=10, evaluation_periods=1, comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                          treat_missing_data=cw.TreatMissingData.NOT_BREACHING)
        down = cw.Alarm(self, "ProbeFailed", alarm_description="The public /healthz failed twice in 10 minutes",
                        metric=cw.Metric(namespace="Foundry/Probe", metric_name="Healthy", statistic="Minimum",
                                         dimensions_map={"Deployment": deployment}, period=Duration.minutes(5)),
                        threshold=1, evaluation_periods=2, comparison_operator=cw.ComparisonOperator.LESS_THAN_THRESHOLD,
                        treat_missing_data=cw.TreatMissingData.BREACHING)
        busy = cw.Alarm(self, "ApiInvocations", alarm_description="Over 5,000 API requests in 15 minutes (a cost guard)",
                        metric=cw.Metric(namespace="AWS/Lambda", metric_name="Invocations", dimensions_map=live,
                                         statistic="Sum", period=Duration.minutes(15)),
                        threshold=5000, evaluation_periods=1, comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                        treat_missing_data=cw.TreatMissingData.NOT_BREACHING)
        for a in (errors, down, busy):
            a.add_alarm_action(alarm)

        for name, value in (("Url", url.url), ("ApiFunction", api.function_name), ("WorkerFunction", worker.function_name),
                            ("StateMachine", machine.state_machine_arn), ("ProbeRule", rule.rule_name),
                            ("ProbeAlarm", down.alarm_name), ("Build", build),
                            ("Cap", "" if cap_usd is None else f"{cap_usd:g}"), ("BatchLogGroup", machine_logs.log_group_name),
                            ("Deployment", deployment)):
            CfnOutput(self, name, value=value)
        _tag(self, deployment)


def _mib(n: int):
    from aws_cdk import Size

    return Size.mebibytes(n)
