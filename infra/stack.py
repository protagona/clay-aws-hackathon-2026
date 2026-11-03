"""Runs bucket + harness role for one sandbox account in us-west-2 (next to NAIP, which is Requester-Pays)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack, Tags
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct

BEDROCK_ACTIONS = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]  # authorize Converse/ConverseStream
CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
# Regions the us. inference profiles route to (aws bedrock get-inference-profile, checked 2026-10-30). A policy that
# names a profile must also allow its foundation model in each of them (Bedrock user guide, inference-profiles-prereq).
PROFILE_REGIONS = ["us-east-1", "us-east-2", "us-west-2"]


MODEL_ID_RE = re.compile(r"^[a-z0-9-]+\.[A-Za-z0-9.:-]+$")


def bedrock_models(config_dir: Path = CONFIG_DIR) -> tuple[list[str], list[str]]:
    """(us. inference profiles, in-region model ids) the Bedrock configs can call: agent, judge, summarizer and
    priced (batch) models."""
    ids: set[str] = set()
    for f in sorted(config_dir.glob("bedrock*.toml")):
        cfg = tomllib.loads(f.read_text())
        ids.update(cfg[k]["id"] for k in ("model", "judge", "summarizer") if cfg.get(k, {}).get("provider") == "bedrock")
        ids.update(cfg.get("prices", {}))
    bad = sorted(i for i in ids if not MODEL_ID_RE.match(i) or i.startswith(("global.", "eu.", "apac.")))
    if bad:
        raise ValueError(f"Not a us. profile or in-region model id: {', '.join(bad)} (in {config_dir}/bedrock*.toml)")
    return sorted(i for i in ids if i.startswith("us.")), sorted(i for i in ids if not i.startswith("us."))


def requester_pays_buckets(config_dir: Path = CONFIG_DIR) -> list[str]:
    """Requester-pays buckets the Bedrock configs' path check may list, signed and billed to this account
    (requirement-gaps slice A). The role gets s3:ListBucket on these buckets only, never GetObject."""
    names: set[str] = set()
    for f in sorted(config_dir.glob("bedrock*.toml")):
        names.update(tomllib.loads(f.read_text()).get("checks", {}).get("requester_pays", []))
    bad = sorted(n for n in names if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", n))
    if bad:
        raise ValueError(f"Not S3 bucket names: {', '.join(bad)} (in {config_dir}/bedrock*.toml)")
    return sorted(names)


RUNS_BUCKET_PREFIX = "foundry-runs-"  # + <account>-<region>: src/foundry/data/storage.py fills in {account}

class FoundryStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        # Grant log delivery with a bucket policy, not the LogDeliveryWrite ACL (ACLs are off on both buckets).
        self.node.set_context("@aws-cdk/aws-s3:serverAccessLogsUseBucketPolicy", True)

        logs = s3.Bucket(
            self, "AccessLogsBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            versioned=True,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(90),
                                              noncurrent_version_expiration=Duration.days(90))],
            removal_policy=RemovalPolicy.RETAIN,
        )
        logs.policy.apply_removal_policy(RemovalPolicy.RETAIN)

        bucket = s3.Bucket(
            self, "RunsBucket",
            # Predictable per account, so the configs name it without being edited: s3_uri = "s3://foundry-runs-
            # {account}-us-west-2" (the mirror fills in the caller's account and checks it owns the bucket).
            bucket_name=f"{RUNS_BUCKET_PREFIX}{self.account}-{self.region}",
            server_access_logs_bucket=logs,
            server_access_logs_prefix="runs-bucket/",  # F-010
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=True,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,  # run history is provenance; never deleted by a stack update
        )

        # Keep the SSL-only policy with the retained bucket, so an orphaned bucket stays protected after destroy.
        bucket.policy.apply_removal_policy(RemovalPolicy.RETAIN)

        role = iam.Role(
            self, "HarnessRole",
            assumed_by=iam.AccountRootPrincipal(),
            description="Runs the Foundry eval sandbox: Bedrock inference, runs/ (read-write) and samples/ (read-only)",
        )
        # F-012: only the inference profiles our configs name, and their models only when reached through them.
        # In-region model ids (no profile) are allowed directly, in this region only.
        profile_ids, regional_ids = bedrock_models()
        profiles = [f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/{p}" for p in profile_ids]
        role.add_to_policy(iam.PolicyStatement(
            actions=BEDROCK_ACTIONS,
            resources=profiles + [f"arn:aws:bedrock:{self.region}::foundation-model/{m}" for m in regional_ids]))
        role.add_to_policy(iam.PolicyStatement(
            actions=BEDROCK_ACTIONS,
            resources=[f"arn:aws:bedrock:{r}::foundation-model/{p.removeprefix('us.')}"
                       for p in profile_ids for r in PROFILE_REGIONS],
            conditions={"StringLike": {"bedrock:InferenceProfileArn": profiles}}))
        role.add_to_policy(iam.PolicyStatement(
            actions=["s3:GetObject", "s3:PutObject"], resources=[bucket.arn_for_objects("runs/*")]))
        # Read-only, pre-staged imagery samples (A-2; scripts/stage_samples.sh): a fallback to reading at the source.
        role.add_to_policy(iam.PolicyStatement(actions=["s3:GetObject"], resources=[bucket.arn_for_objects("samples/*")]))
        role.add_to_policy(iam.PolicyStatement(
            actions=["s3:ListBucket"], resources=[bucket.bucket_arn],
            conditions={"StringLike": {"s3:prefix": ["runs/*", "samples/*"]}}))
        if rp := requester_pays_buckets():  # path check: signed lists of NAIP (requester pays), list only
            role.add_to_policy(iam.PolicyStatement(actions=["s3:ListBucket"],
                                                   resources=[f"arn:aws:s3:::{b}" for b in rp]))

        Tags.of(self).add("project", "clay-foundry")
        Tags.of(self).add("owner", "team-46")

        CfnOutput(self, "RunsBucketName", value=bucket.bucket_name,
                  description='Set [storage] s3_uri = "s3://<this>" in config to mirror runs')
        CfnOutput(self, "HarnessRoleArn", value=role.role_arn)
