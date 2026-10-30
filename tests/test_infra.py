"""AC22: the synthesized template, asserted without the CDK CLI, Docker or AWS credentials."""

import json
import re
import sys
from pathlib import Path

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra"))
from stack import FoundryStack  # noqa: E402


def _template() -> Template:
    app = cdk.App()
    stack = FoundryStack(app, "Test", env=cdk.Environment(region="us-west-2"))
    assert stack.region == "us-west-2"
    return Template.from_stack(stack)


def _statements(t: Template) -> list[dict]:
    policies = t.find_resources("AWS::IAM::Policy")
    return [s for p in policies.values() for s in p["Properties"]["PolicyDocument"]["Statement"]]


def test_bucket_is_private_encrypted_versioned_ssl_only():
    t = _template()
    t.resource_count_is("AWS::S3::Bucket", 1)
    t.has_resource_properties("AWS::S3::Bucket", {
        "PublicAccessBlockConfiguration": {"BlockPublicAcls": True, "BlockPublicPolicy": True,
                                           "IgnorePublicAcls": True, "RestrictPublicBuckets": True},
        "BucketEncryption": {"ServerSideEncryptionConfiguration": [
            {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]},
        "VersioningConfiguration": {"Status": "Enabled"},
    })
    t.has_resource("AWS::S3::Bucket", {"DeletionPolicy": "Retain"})
    t.has_resource("AWS::S3::BucketPolicy", {"DeletionPolicy": "Retain"})  # stays with the retained bucket
    t.has_resource_properties("AWS::S3::BucketPolicy", {"PolicyDocument": {"Statement": Match.array_with([
        Match.object_like({"Effect": "Deny", "Condition": {"Bool": {"aws:SecureTransport": "false"}}})])}})


def test_role_policy_is_exact():
    t = _template()
    t.resource_count_is("AWS::IAM::Role", 1)
    stmts = _statements(t)
    actions = sorted(a for s in stmts for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"]))
    assert actions == ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                       "s3:GetObject", "s3:GetObject", "s3:ListBucket", "s3:PutObject"]
    s3_stmts = [s for s in stmts if any(a.startswith("s3:") for a in
                                        ([s["Action"]] if isinstance(s["Action"], str) else s["Action"]))]
    for s in s3_stmts:
        text = json.dumps(s["Resource"])
        assert "RunsBucket" in text  # only the runs bucket
        if "s3:ListBucket" in json.dumps(s["Action"]):
            assert s["Condition"] == {"StringLike": {"s3:prefix": ["runs/*", "samples/*"]}}
        elif "/samples/*" in text:
            assert s["Action"] == "s3:GetObject"  # samples are read-only for the harness
        else:
            assert "/runs/*" in text


def test_tags_and_no_hardcoded_account():
    t = _template()
    role = next(iter(t.find_resources("AWS::IAM::Role").values()))
    tags = {tag["Key"]: tag["Value"] for tag in role["Properties"]["Tags"]}
    assert tags == {"project": "clay-foundry", "owner": "team-46"}
    assert not re.search(r"\b\d{12}\b", json.dumps(t.to_json()))
