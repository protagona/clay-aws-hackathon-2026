"""S3 mirror (AC20) against stubbed S3 clients."""

import json

import boto3
import pytest
from botocore.stub import ANY, Stubber
from harness_helpers import PROMPT, write_config

from foundry.data import storage
from foundry.config import StorageCfg, load_config
from foundry.engine.harness.runner import run


@pytest.fixture(autouse=True)
def fake_creds(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")


def test_unset_uri_creates_no_client(tmp_path, fixture_run, monkeypatch):
    monkeypatch.setattr(storage, "make_client", lambda *a: (_ for _ in ()).throw(AssertionError("client created")))
    storage.mirror(fixture_run("clean"), StorageCfg())


def test_uploads_every_regular_file(tmp_path, fixture_run, monkeypatch):
    run_dir = fixture_run("clean")
    (run_dir / ".evaluating").write_text("{}")
    (run_dir / ".eval-abc").mkdir()
    client = boto3.client("s3", region_name="us-west-2")
    stub = Stubber(client)
    for name in ("output.json", "run.json", "trace.jsonl"):
        stub.add_response("put_object", {}, {"Bucket": "my-runs", "Key": f"runs/{run_dir.name}/{name}", "Body": ANY})
    monkeypatch.setattr(storage, "make_client", lambda t: client)
    with stub:
        storage.mirror(run_dir, StorageCfg(s3_uri="s3://my-runs"))
        stub.assert_no_pending_responses()


def test_an_account_bucket_is_the_callers_own_and_checked_as_such(fixture_run, monkeypatch):  # every sandbox
    run_dir = fixture_run("clean")
    client = boto3.client("s3", region_name="us-west-2")
    stub = Stubber(client)
    for name in ("output.json", "run.json", "trace.jsonl"):
        stub.add_response("put_object", {}, {"Bucket": "foundry-runs-222233334444-us-west-2", "Body": ANY,
                                              "Key": f"runs/{run_dir.name}/{name}", "ExpectedBucketOwner": "222233334444"})
    monkeypatch.setattr(storage, "make_client", lambda t: client)
    monkeypatch.setattr(storage, "caller_account", lambda t: "222233334444")
    with stub:
        storage.mirror(run_dir, StorageCfg(s3_uri="s3://foundry-runs-{account}-us-west-2"))
        stub.assert_no_pending_responses()


def test_the_account_placeholder_is_the_only_one_allowed():
    assert StorageCfg(s3_uri="s3://foundry-runs-{account}-us-west-2").s3_uri == "s3://foundry-runs-{account}-us-west-2"
    for bad in ("s3://foundry-{region}", "s3://{account}/runs", "s3://Foundry-{account}"):
        with pytest.raises(ValueError):
            StorageCfg(s3_uri=bad)


def test_upload_failure_warns_and_keeps_files(tmp_path, fixture_run, monkeypatch, capsys):
    run_dir = fixture_run("clean")
    client = boto3.client("s3", region_name="us-west-2")
    stub = Stubber(client)
    stub.add_client_error("put_object", service_error_code="AccessDenied", http_status_code=403)
    monkeypatch.setattr(storage, "make_client", lambda t: client)
    with stub:
        storage.mirror(run_dir, StorageCfg(s3_uri="s3://my-runs"))
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "S3 upload to s3://my-runs failed" in err[0] and "AccessDenied" in err[0]
    assert (run_dir / "run.json").exists()


def test_upload_at_run_end_and_after_evaluation_never_at_start(tmp_path, monkeypatch):
    from eval_helpers import eval_config, load

    from foundry.engine.evaluate.evaluate import evaluate

    uploads = []

    class Recording:
        def put_object(self, Bucket, Key, Body):
            run_dir = tmp_path / "runs" / Key.split("/")[1]
            uploads.append((Key.split("/")[-1], json.loads((run_dir / "run.json").read_text())["status"]))

    monkeypatch.setattr(storage, "make_client", lambda t: Recording())
    cfg_path = write_config(tmp_path, [{"tool": "echo", "input": {"text": "x"}}, {"text": "done"}],
                            extra='\n[storage]\ns3_uri = "s3://my-runs"\n')
    run_dir, _ = run(load_config(cfg_path), PROMPT, tmp_path / "runs")
    assert uploads and all(status == "succeeded" for _, status in uploads)
    assert {name for name, _ in uploads} == {"run.json", "trace.jsonl", "output.json", "mcp-fault.log"}
    n = len(uploads)
    evaluate(run_dir, load(eval_config(tmp_path / "e", extra='[storage]\ns3_uri = "s3://my-runs"\n')))
    assert {name for name, _ in uploads[n:]} >= {"scores.json", "report.md", "rubric.json"}
