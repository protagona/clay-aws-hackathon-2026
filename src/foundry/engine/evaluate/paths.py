"""Path check: does each S3 location the agent's output cites exist? (clay-embeddings design, path check.)

Pulls S3 locations out of the final output and lists each one with a single anonymous ListObjectsV2 call:
`found`, `not_found`, or `unverified` (access denied, requester-pays, timeouts, limits). Requests are unsigned
(no identity, no requester-pays charges), go only to S3 endpoints boto3 derives from a validated bucket name,
and never read object contents (design ADR-2). The result is evidence for the judge and the report; it doesn't
change `overall` (ADR-1).
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Callable, NamedTuple
from urllib.parse import unquote

VERSION = "v1"
LIMIT = 25
BUDGET_S = 30.0
MAX_KEY_CHARS = 512
WORKERS = 8

FOUND, NOT_FOUND, UNVERIFIED = "found", "not_found", "unverified"

_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_REGION = r"(?:[.-][a-z0-9-]+)?"
_KEY = r"([A-Za-z0-9._\-/=%]*)"  # % only so https keys can be percent-decoded; see _clean_key
_PATTERNS = [  # (regex, is_https); group 1 = bucket, group 2 = key (may be empty)
    (re.compile(r"s3://([A-Za-z0-9.\-_]+)/?" + _KEY), False),
    (re.compile(r"arn:aws:s3:::([A-Za-z0-9.\-_]+)/?" + _KEY), False),
    (re.compile(r"https?://s3" + _REGION + r"\.amazonaws\.com/([A-Za-z0-9.\-_]+)/?" + _KEY), True),
    (re.compile(r"https?://([A-Za-z0-9.\-_]+)\.s3" + _REGION + r"\.amazonaws\.com/?" + _KEY), True),
]
# Keys keep only these characters (hive partitions need "="). Cutting early is harmless - a shorter prefix of a real
# path still lists as found - while cutting late turns a real location into not_found, so anything else ends the key:
# markdown, quotes, lists, query strings, fragments, placeholders, dashes, non-ASCII.
_KEY_SAFE = re.compile(r"[A-Za-z0-9._\-/=]*")
_TRAIL = ".,;:)]'\"|_-"

Lister = Callable[[str, str], tuple[str, str]]


class S3Ref(NamedTuple):
    bucket: str
    key: str

    @property
    def location(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


def enabled(cfg) -> bool:
    """On unless `[checks] paths = false` or FOUNDRY_PATH_CHECK=off (the variable reaches subprocesses)."""
    return bool(cfg.checks.paths) and os.environ.get("FOUNDRY_PATH_CHECK", "").strip().lower() != "off"


def _clean_key(key: str, is_https: bool) -> str:
    if is_https:
        key = unquote(key)
    key = key.split("...", 1)[0]
    key = _KEY_SAFE.match(key).group(0)
    return key.rstrip(_TRAIL)


def extract(text: str) -> list[S3Ref]:
    """Distinct (bucket, key) pairs in first-seen order. Placeholders cut the key; invalid buckets are dropped."""
    hits: list[tuple[int, S3Ref]] = []
    taken: list[tuple[int, int]] = []
    for pattern, is_https in _PATTERNS:
        for m in pattern.finditer(text or ""):
            if any(a <= m.start() < b for a, b in taken):
                continue  # an https form already matched as another pattern's span (or vice versa)
            bucket = m.group(1).rstrip(_TRAIL) if m.group(2) == "" else m.group(1)
            key = _clean_key(m.group(2), is_https)
            if not _BUCKET.match(bucket) or len(key) > MAX_KEY_CHARS:
                continue
            taken.append((m.start(), m.end()))
            hits.append((m.start(), S3Ref(bucket, key)))
    seen: set[S3Ref] = set()
    out = []
    for _, ref in sorted(hits, key=lambda h: h[0]):
        if ref not in seen:
            seen.add(ref)
            out.append(ref)
    return out


def default_lister() -> Lister:
    """One unsigned S3 client; (bucket, prefix) -> (status, reason). One attempt, 3 s connect and read timeouts."""
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config
    from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError

    client = boto3.client("s3", region_name="us-east-1", config=Config(
        signature_version=UNSIGNED, connect_timeout=3, read_timeout=3, retries={"total_max_attempts": 1}))

    def lister(bucket: str, prefix: str) -> tuple[str, str]:
        try:
            r = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
        except ClientError as exc:
            err = exc.response.get("Error", {})
            code, message = err.get("Code", "Unknown"), err.get("Message", "")
            if code == "NoSuchBucket":
                return NOT_FOUND, "bucket does not exist"
            if code == "AccessDenied":
                return UNVERIFIED, "access denied (Requester Pays)" if "requester pays" in message.lower() \
                    else "access denied"
            return UNVERIFIED, f"error: {code}"
        except (ConnectTimeoutError, ReadTimeoutError):
            return UNVERIFIED, "timed out"
        except Exception as exc:  # noqa: BLE001 - any failure is "couldn't verify", never a crash
            return UNVERIFIED, f"error: {type(exc).__name__}"
        if r.get("KeyCount", 0) >= 1:
            return FOUND, ""
        return (FOUND, "bucket exists (empty)") if prefix == "" else (NOT_FOUND, "no object with this prefix")

    return lister


def check(refs: list[S3Ref], lister: Lister | None = None, limit: int = LIMIT, budget_s: float = BUDGET_S) -> dict:
    """List up to `limit` refs in parallel within `budget_s`; everything else is `unverified` with the reason."""
    items = [{"location": r.location, "status": UNVERIFIED, "reason": f"not checked (limit of {limit})"}
             for r in refs]
    todo = refs[:limit]
    if todo:
        lister = lister or default_lister()
        pool = ThreadPoolExecutor(max_workers=WORKERS)
        futures = {pool.submit(lister, r.bucket, r.key): i for i, r in enumerate(todo)}
        done, _ = wait(futures, timeout=budget_s)
        for fut, i in futures.items():
            if fut in done:
                try:
                    status, reason = fut.result()
                except Exception as exc:  # noqa: BLE001 - a lister that raises means "couldn't verify"
                    status, reason = UNVERIFIED, f"error: {type(exc).__name__}"
            else:
                status, reason = UNVERIFIED, "not checked (time budget)"
            items[i].update(status=status, reason=reason)
        pool.shutdown(wait=False, cancel_futures=True)
    counts = {s: sum(1 for it in items if it["status"] == s) for s in (FOUND, NOT_FOUND, UNVERIFIED)}
    return {"version": VERSION, "checked": len(todo), **counts, "items": items}
