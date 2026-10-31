"""AC22: the synthesized template, asserted without the CDK CLI, Docker or AWS credentials."""

import json
import re
import sys
from pathlib import Path

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra"))
from stack import PROFILE_REGIONS, FoundryStack, bedrock_models  # noqa: E402


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
    t.resource_count_is("AWS::S3::Bucket", 2)  # runs + access logs
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
    assert actions == ["bedrock:InvokeModel", "bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                       "bedrock:InvokeModelWithResponseStream",
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


def _arn(v) -> str:
    """An ARN as text: plain strings as-is, Fn::Join parts concatenated with tokens shown as <Ref>."""
    if isinstance(v, str):
        return v
    sep, parts = v["Fn::Join"]
    return sep.join(p if isinstance(p, str) else f"<{next(iter(p.values()))}>" for p in parts)


def test_bedrock_scoped_to_configured_models():  # increment 6, H1 (F-012)
    profile_ids, regional_ids = bedrock_models()
    assert "us.moonshotai.kimi-k3" in profile_ids and "us.anthropic.claude-opus-4-6-v1" in profile_ids
    stmts = [s for s in _statements(_template()) if "bedrock:InvokeModel" in s["Action"]]
    assert len(stmts) == 2 and all("*" not in json.dumps(s["Resource"]) for s in stmts)
    direct = next(s for s in stmts if "Condition" not in s)
    via = next(s for s in stmts if "Condition" in s)
    profiles = sorted(f"arn:aws:bedrock:us-west-2:<AWS::AccountId>:inference-profile/{p}" for p in profile_ids)
    assert sorted(_arn(r) for r in direct["Resource"]) == sorted(
        profiles + [f"arn:aws:bedrock:us-west-2::foundation-model/{m}" for m in regional_ids])
    assert sorted(_arn(r) for r in via["Resource"]) == sorted(
        f"arn:aws:bedrock:{r}::foundation-model/{p.removeprefix('us.')}" for p in profile_ids for r in PROFILE_REGIONS)
    assert sorted(_arn(r) for r in via["Condition"]["StringLike"]["bedrock:InferenceProfileArn"]) == profiles


def test_bedrock_models_rejects_unknown_id_shapes(tmp_path):  # H1
    (tmp_path / "bedrock.toml").write_text('[model]\nprovider = "bedrock"\nid = "eu.anthropic.x"\n')
    import pytest

    with pytest.raises(ValueError, match="eu.anthropic.x"):
        bedrock_models(tmp_path)


def test_runs_bucket_access_logging():  # H2 (F-010)
    t = _template()
    buckets = t.find_resources("AWS::S3::Bucket")
    runs = next(b for k, b in buckets.items() if k.startswith("RunsBucket"))
    logs_id = next(k for k in buckets if k.startswith("AccessLogsBucket"))
    assert runs["Properties"]["LoggingConfiguration"] == {"DestinationBucketName": {"Ref": logs_id},
                                                          "LogFilePrefix": "runs-bucket/"}
    logs = buckets[logs_id]
    assert logs["DeletionPolicy"] == "Retain"
    assert logs["Properties"]["LifecycleConfiguration"]["Rules"][0]["ExpirationInDays"] == 90
    assert logs["Properties"]["PublicAccessBlockConfiguration"]["BlockPublicPolicy"] is True
    assert logs["Properties"]["BucketEncryption"]["ServerSideEncryptionConfiguration"][0][
        "ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"
