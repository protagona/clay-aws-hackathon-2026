"""S3 mirror: copy a run directory to s3://<bucket>/runs/<run_id>/ when [storage] s3_uri is set."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

NO_BUCKET_QUIET_S = 3600  # after a missing-bucket note, how long runs skip the mirror quietly


def make_client(timeout_s: float):
    import boto3
    from botocore.config import Config

    cfg = Config(connect_timeout=timeout_s, read_timeout=timeout_s, retries={"max_attempts": 2, "mode": "standard"})
    return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-west-2"), config=cfg)


def files_to_upload(run_dir: Path) -> list[Path]:
    """Top-level regular files only; dotfiles (.evaluating, .eval-*) are never mirrored."""
    return sorted(p for p in run_dir.iterdir() if p.is_file() and not p.name.startswith("."))


def caller_account(timeout_s: float) -> str:
    import boto3
    from botocore.config import Config

    cfg = Config(connect_timeout=timeout_s, read_timeout=timeout_s, retries={"max_attempts": 2, "mode": "standard"})
    return boto3.client("sts", region_name=os.environ.get("AWS_REGION", "us-west-2"), config=cfg).get_caller_identity()["Account"]


def mirror(run_dir: Path, storage_cfg) -> None:
    """Upload the run's files. A bucket named with "{account}" is the caller's own account's (infra/stack.py names it
    so): the account is filled in from the credentials, and S3 refuses the upload if anyone else owns that bucket."""
    if not storage_cfg.s3_uri:
        return  # no AWS client is created
    bucket = storage_cfg.s3_uri.removeprefix("s3://").rstrip("/")
    run_dir = Path(run_dir)
    owner, marker = {}, None
    try:
        if "{account}" in bucket:
            account = caller_account(storage_cfg.s3_timeout_s)
            bucket = bucket.replace("{account}", account)
            owner = {"ExpectedBucketOwner": account}
            marker = run_dir.parent / f".no-bucket-{bucket}"
            if marker.exists() and time.time() - marker.stat().st_mtime < NO_BUCKET_QUIET_S:
                return  # this account has no laptop stack (optional): said so once, recheck later
        client = make_client(storage_cfg.s3_timeout_s)
        for path in files_to_upload(run_dir):
            client.put_object(Bucket=bucket, Key=f"runs/{run_dir.name}/{path.name}", Body=path.read_bytes(), **owner)
    except Exception as exc:
        if marker is not None and getattr(exc, "response", {}).get("Error", {}).get("Code") == "NoSuchBucket":
            marker.touch()
            print(f"note: no s3://{bucket} in this account, so runs stay local only (optional: make laptop-stack "
                  f"creates it). Not repeated for an hour.", file=sys.stderr)
            return
        print(f"S3 upload to s3://{bucket} failed: {type(exc).__name__}: {str(exc)[:300]}; "
              f"local files are kept in {run_dir}", file=sys.stderr)
