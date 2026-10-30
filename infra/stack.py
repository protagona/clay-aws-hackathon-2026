"""Runs bucket + harness role for one sandbox account in us-west-2 (next to NAIP, which is Requester-Pays)."""

from __future__ import annotations

from aws_cdk import CfnOutput, RemovalPolicy, Stack, Tags
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct

BEDROCK_ACTIONS = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]  # authorize Converse/ConverseStream


class FoundryStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        bucket = s3.Bucket(
            self, "RunsBucket",
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
        role.add_to_policy(iam.PolicyStatement(actions=BEDROCK_ACTIONS, resources=["*"]))
        role.add_to_policy(iam.PolicyStatement(
            actions=["s3:GetObject", "s3:PutObject"], resources=[bucket.arn_for_objects("runs/*")]))
        # Read-only, pre-staged imagery samples (A-2; scripts/stage_samples.sh): a fallback to reading at the source.
        role.add_to_policy(iam.PolicyStatement(actions=["s3:GetObject"], resources=[bucket.arn_for_objects("samples/*")]))
        role.add_to_policy(iam.PolicyStatement(
            actions=["s3:ListBucket"], resources=[bucket.bucket_arn],
            conditions={"StringLike": {"s3:prefix": ["runs/*", "samples/*"]}}))

        Tags.of(self).add("project", "clay-foundry")
        Tags.of(self).add("owner", "team-46")

        CfnOutput(self, "RunsBucketName", value=bucket.bucket_name,
                  description='Set [storage] s3_uri = "s3://<this>" in config to mirror runs')
        CfnOutput(self, "HarnessRoleArn", value=role.role_arn)
