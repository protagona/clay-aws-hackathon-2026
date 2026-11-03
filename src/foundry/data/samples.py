"""Staged imagery samples (requirement-gaps design, sample cache): a directory holding sample.tif and tiles.json.

Lookup order for a sample <name>:
  1. $FOUNDRY_SAMPLES_DIR/<name>/, then <project root>/samples/<name>/ (the staging script's local output): used as is.
  2. The runs bucket ($STATE_BUCKET in the cloud, else [storage] s3_uri) under samples/<name>/, cached in
     <tempdir>/foundry-samples/<name>/ and refreshed when the objects' ETags change (two HEADs per call).
  3. A bucket that can't be reached falls back to an existing cache; with no cache, None.

Only trusted code calls this (the runner, the evaluator); MCP servers get the resulting local path, never credentials.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

FILES = ("sample.tif", "tiles.json")
_NAME_OK = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def _complete(d: Path) -> bool:
    return all((d / f).is_file() and (d / f).stat().st_size > 0 for f in FILES)


def cache_dir(name: str) -> Path:
    return Path(tempfile.gettempdir()) / "foundry-samples" / name


def bucket_for(storage_cfg) -> tuple[str, dict] | None:
    """(bucket, extra S3 args) for the runs bucket, or None when there is none."""
    if os.environ.get("STATE_BUCKET"):
        return os.environ["STATE_BUCKET"], {}
    uri = getattr(storage_cfg, "s3_uri", "") or ""
    if not uri.startswith("s3://"):
        return None
    bucket = uri[5:].split("/", 1)[0]
    extra: dict = {}
    if "{account}" in bucket:
        from foundry.data.storage import caller_account

        account = caller_account(getattr(storage_cfg, "s3_timeout_s", 10))
        bucket, extra = bucket.replace("{account}", account), {"ExpectedBucketOwner": account}
    return bucket, extra


def _client():
    import boto3
    from botocore.config import Config

    return boto3.client("s3", config=Config(connect_timeout=5, read_timeout=30,
                                            retries={"mode": "standard", "total_max_attempts": 2}))


def _atomic_write(dest: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _local_bases() -> list[Path]:
    """$FOUNDRY_SAMPLES_DIR, then the project's samples/ (the staging script's local output)."""
    from foundry.config import project_root

    return [Path(b) for b in (os.environ.get("FOUNDRY_SAMPLES_DIR"), str(project_root() / "samples")) if b]


def ensure(name: str, storage_cfg=None, client=None) -> Path | None:
    """A local directory with the sample's files, or None if it isn't available anywhere."""
    if not name or not set(name) <= _NAME_OK:
        raise ValueError(f"bad sample name {name!r}")
    for base in _local_bases():
        if _complete(base / name):
            return base / name
    cache = cache_dir(name)
    target = bucket_for(storage_cfg)
    if target is None:
        return cache if _complete(cache) else None
    bucket, extra = target
    try:
        s3 = client or _client()
        tags = {f: s3.head_object(Bucket=bucket, Key=f"samples/{name}/{f}", **extra)["ETag"] for f in FILES}
        cache.mkdir(parents=True, exist_ok=True)
        etag_file = cache / ".etags.json"
        try:
            have = json.loads(etag_file.read_text())
        except (OSError, ValueError):
            have = {}
        if have != tags or not _complete(cache):
            for f in FILES:
                body = s3.get_object(Bucket=bucket, Key=f"samples/{name}/{f}", **extra)["Body"].read()
                _atomic_write(cache / f, body)
            _atomic_write(etag_file, json.dumps(tags).encode())
        return cache
    except Exception as exc:  # noqa: BLE001 - not staged, no access, offline: fall back, never crash a run
        if _complete(cache):
            print(f"note: sample {name}: s3://{bucket}/samples/{name}/ unreachable ({type(exc).__name__}); "
                  "using the cached copy", file=sys.stderr)
            return cache
        return None


def staging_hint(name: str, storage_cfg=None) -> str:
    """The run note when a sample is missing (no AWS calls: it may be written because AWS is unreachable)."""
    uri = os.environ.get("STATE_BUCKET") and f"s3://{os.environ['STATE_BUCKET']}"
    uri = uri or (getattr(storage_cfg, "s3_uri", "") or "")
    where = f"{uri.rstrip('/')}/samples/{name}/" if uri.startswith("s3://") else "any runs bucket (none configured)"
    return (f"sample {name} is not staged in {where} (run: scripts/stage_naip_tiles.py --upload <deployment>, "
            f"or stage locally into samples/{name}/)")
