"""Synth checks for the serverless stacks (aws-deploy slices 5, 9): shapes CloudFormation will deploy."""

import json
import sys

import pytest
from conftest import ROOT

cdk = pytest.importorskip("aws_cdk")
sys.path.insert(0, str(ROOT / "infra"))


def definition_of(machine_stack) -> dict:
    from aws_cdk.assertions import Template

    t = Template.from_stack(machine_stack).to_json()
    (sm,) = [r for r in t["Resources"].values() if r["Type"] == "AWS::StepFunctions::StateMachine"]
    parts = sm["Properties"]["DefinitionString"]["Fn::Join"][1]
    text = "".join(p if isinstance(p, str) else "ARN" for p in parts)
    return json.loads(text)


@pytest.fixture(scope="module")
def batch_definition():
    from aws_cdk import App, Stack
    from aws_cdk import aws_lambda as lam
    from foundry_cloud.batch_sm import batch_state_machine

    app = App()
    stack = Stack(app, "T", env={"account": "111111111111", "region": "us-west-2"})
    fn = lam.Function(stack, "W", runtime=lam.Runtime.PYTHON_3_12, handler="x.h", code=lam.Code.from_inline("x"))
    batch_state_machine(stack, fn)
    return definition_of(stack)


def test_batch_items_never_retry_paid_work_and_always_catch(batch_definition):
    states = batch_definition["States"]
    items = states["Items"]
    assert items["Type"] == "Map" and items["MaxConcurrencyPath"] == "$.concurrency"
    item = items["ItemProcessor"]["States"]["Item"]
    assert [r["ErrorEquals"] for r in item.get("Retry", [])] == [["Lambda.TooManyRequestsException"]]
    (catch,) = item["Catch"]
    assert catch["ErrorEquals"] == ["States.ALL"] and catch["ResultPath"] == "$.error"  # keeps the item's input
    assert "ToleratedFailurePercentage" not in items  # inline Map has none: the per-item Catch does the job


def test_every_path_settles_the_batch(batch_definition):
    states = batch_definition["States"]
    assert states["Prepare"]["Catch"][0]["Next"] == "KeepBatchId" and states["Items"]["Catch"][0]["Next"] == "KeepBatchId"
    assert states["KeepBatchId"]["Next"] == "Finalize" and states["Items"]["Next"] == "FinalizeOk"
    assert states["Finalize"].get("End") and states["FinalizeOk"].get("End")
