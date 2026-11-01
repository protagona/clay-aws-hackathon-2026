"""The table and bucket side of scripts/cloud.sh (aws-deploy slice 9): tokens, the session key, seeding, gc, the
live-job check and the operator's CLI login. cloud.sh passes the deployment's bucket and table names.

    uv run python scripts/cloud_ops.py --bucket B --table T token-add <name>     # prints the token once
    uv run python scripts/cloud_ops.py --bucket B --table T token-list | token-revoke <name>
    uv run python scripts/cloud_ops.py --bucket B --table T session-key           # creates it once
    uv run python scripts/cloud_ops.py --bucket B --table T seed [--from <dir>]   # local runs/ and tasks/ (spec S4)
    uv run python scripts/cloud_ops.py --bucket B --table T gc [--yes]            # unreferenced objects
    uv run python scripts/cloud_ops.py --bucket B --table T live-jobs             # how many jobs are in flight
    uv run python scripts/cloud_ops.py remote-json <url>                          # the operator's CLI login (D4)
    uv run python scripts/cloud_ops.py reindex <worker function>                  # rebuild stored board cards
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path

from foundry.data.mirror import Mirror
from foundry.data.store import Cond, Conflict, Delete, Put
from foundry.services.access import sha256

REPO = Path(__file__).resolve().parents[1]
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
GC_GRACE_S = 3600  # an object younger than this may belong to a push that hasn't committed yet


# --- tokens and the session key (aws-deploy ADR-9) ----------------------------------------------------------------


def token_add(store, name: str) -> str:
    if not NAME_RE.match(name):
        raise SystemExit("A token name is 1-40 of a-z, 0-9, '.', '_' or '-', starting with a letter or digit.")
    token = "fdy_" + secrets.token_urlsafe(32)
    try:
        store.write(Put({"pk": "TOKEN", "sk": name, "sha256": sha256(token), "created_at": int(time.time())},
                        cond=Cond.absent()))
    except Conflict:
        raise SystemExit(f"A token named {name} already exists; revoke it first.") from None
    return token


def token_list(store) -> list[dict]:
    return [{"name": it["sk"], "created_at": time.strftime("%Y-%m-%d %H:%M", time.gmtime(it.get("created_at", 0)))}
            for it in store.query("TOKEN")]


def token_revoke(store, name: str) -> bool:
    try:
        store.write(Delete("TOKEN", name, cond=Cond.eq("sk", name)))
    except Conflict:
        return False
    return True


def session_key(store) -> bool:
    """Create the key that signs session cookies, once. False if it already existed (it is never rotated here:
    rotating it signs everyone out)."""
    try:
        store.write(Put({"pk": "CONFIG", "sk": "session_key", "key": secrets.token_urlsafe(32)}, cond=Cond.absent()))
    except Conflict:
        return False
    return True


# --- seed (spec S4) ----------------------------------------------------------------------------------------------


def seed(store, source: Path = REPO, log=print) -> int:
    """Copy `source`/runs (runs and batches) and `source`/tasks into an empty deployment. Returns the number of runs
    pushed; 0 and a message if the deployment already holds a run (the seed is all or nothing)."""
    from foundry.services import workers
    from foundry.services.workspace import Workspace

    if store.query("TREE", prefix="run#"):
        log("The deployment already holds runs; the seed is skipped as a whole (spec S4).")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        runs = stage / "runs"
        runs.mkdir()
        src_runs = source / "runs"
        run_ids = sorted(p.name for p in src_runs.iterdir() if p.is_dir() and not p.name.startswith(".")) \
            if src_runs.is_dir() else []
        for rid in run_ids:
            shutil.copytree(src_runs / rid, runs / rid)
        batch_ids = sorted(p.name for p in (src_runs / ".batches").iterdir() if p.is_dir()) \
            if (src_runs / ".batches").is_dir() else []
        for bid in batch_ids:
            shutil.copytree(src_runs / ".batches" / bid, runs / ".batches" / bid)
        tasks = sorted(p.name for p in (source / "tasks").iterdir() if p.is_dir())
        for task in tasks:
            shutil.copytree(source / "tasks" / task, stage / "project" / "tasks" / task,
                            ignore=shutil.ignore_patterns("drafts"))
        mirror = Mirror(store, stage)
        for task in tasks:  # replaces the image's default project files, if a cold start seeded them already
            mirror.push_dir(f"project#{task}")
        ws = Workspace(runs, source, 0)
        for i, rid in enumerate(run_ids, 1):
            workers.index_run(ws, mirror, rid)
            if i % 10 == 0 or i == len(run_ids):
                log(f"  runs: {i}/{len(run_ids)}")
        for bid in batch_ids:
            mirror.push_dir(f"batch#{bid}")
        log(f"Seeded {len(run_ids)} runs, {len(batch_ids)} batches and {len(tasks)} task(s) from {source}.")
        return len(run_ids)


# --- gc ----------------------------------------------------------------------------------------------------------


def unreferenced(store, listing, now: float) -> list[str]:
    """Keys under runs/, project/ and files/ that no tree's manifest names (left by a push that lost its commit
    race), older than GC_GRACE_S. `listing`: (key, last_modified epoch seconds) pairs of current objects."""
    referenced = {e["key"] for it in store.query("TREE") for e in json.loads(it.get("manifest") or "{}").values()}
    return sorted(k for k, at in listing if k.startswith(("runs/", "project/", "files/")) and k not in referenced
                  and now - at > GC_GRACE_S)


def s3_listing(store):
    for page in store.s3.get_paginator("list_objects_v2").paginate(Bucket=store.bucket):
        for o in page.get("Contents", []):
            yield o["Key"], o["LastModified"].timestamp()


# --- the live-job check and the operator's login -------------------------------------------------------------


def live_jobs(store, now: float) -> int:
    return sum(1 for it in store.query("JOB") if it.get("status") in ("queued", "running")
               and float(it.get("deadline", 2**40)) > now and not it.get("parent"))


def remote_json(url: str) -> Path:
    """Point the operator's CLI login at this deployment, keeping the saved token (tokens survive a redeploy)."""
    sys.path.insert(0, str(REPO / "src"))
    from foundry.cli_remote import config_path, save_login

    try:
        token = json.loads(config_path().read_text()).get("token", "")
    except (OSError, ValueError):
        token = ""
    return save_login(url.rstrip("/"), token)


def reindex(function: str, log=print) -> int:
    """Invoke the worker's re-index page by page (backend-parity ADR-3) and print the totals. One synchronous invoke
    per page of at most 600 s: botocore's 60 s default read timeout is raised past the worker's 900 s limit, and
    retries are off, so a slow page is never invoked twice by the client."""
    import boto3
    from botocore.config import Config

    client = boto3.client("lambda", config=Config(read_timeout=910, retries={"total_max_attempts": 1}))
    totals, after, started = {"runs": 0, "updated": 0, "skipped": 0, "busy": 0}, None, time.time()
    while True:
        r = client.invoke(FunctionName=function, Qualifier="live",
                          Payload=json.dumps({"kind": "reindex", "after": after, "budget_s": 600}).encode())
        out = json.loads(r["Payload"].read() or b"null")
        if r.get("FunctionError") or not isinstance(out, dict):
            log(f"The worker failed: {out}")
            return 1
        totals = {k: totals[k] + int(out.get(k) or 0) for k in totals}
        after = out.get("next")
        log(f"  {totals['runs']} runs checked" + (f", continuing after {after}" if after else ""))
        if not after:
            break
    log(f"Re-indexed in {time.time() - started:.0f} s: {totals['runs']} runs, {totals['updated']} updated, "
        f"{totals['skipped']} skipped (live), {totals['busy']} changed meanwhile (run it again to retry them)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--bucket")
    p.add_argument("--table")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("token-add", "token-revoke"):
        sub.add_parser(name).add_argument("name")
    for name in ("token-list", "session-key", "live-jobs"):
        sub.add_parser(name)
    sub.add_parser("seed").add_argument("--from", dest="source", default=str(REPO))
    sub.add_parser("gc").add_argument("--yes", action="store_true", help="delete (default: list only)")
    sub.add_parser("remote-json").add_argument("url")
    sub.add_parser("reindex").add_argument("function")
    a = p.parse_args(argv)
    if a.cmd == "remote-json":
        print(f"CLI login now points at {a.url} ({remote_json(a.url)})")
        return 0
    if a.cmd == "reindex":
        return reindex(a.function)
    if not (a.bucket and a.table):
        p.error("--bucket and --table are required")
    from foundry.data.store import AwsStore

    store = AwsStore(a.bucket, a.table)
    if a.cmd == "token-add":
        token = token_add(store, a.name)
        print(f"Token for {a.name} (shown once; share it privately):\n{token}")
    elif a.cmd == "token-list":
        rows = token_list(store)
        print("\n".join(f"{r['name']:<24} created {r['created_at']} UTC" for r in rows) or "No tokens.")
    elif a.cmd == "token-revoke":
        print(f"Revoked {a.name} (stops working within 60 s)." if token_revoke(store, a.name) else f"No token named {a.name}.")
    elif a.cmd == "session-key":
        print("Created the session key." if session_key(store) else "The session key exists.")
    elif a.cmd == "live-jobs":
        print(live_jobs(store, time.time()))
    elif a.cmd == "seed":
        seed(store, Path(a.source))
    elif a.cmd == "gc":
        keys = unreferenced(store, s3_listing(store), time.time())
        print(f"{len(keys)} unreferenced object(s){'' if a.yes or not keys else ' (pass --yes to delete)'}")
        for k in keys:
            print(f"  {k}")
            if a.yes:
                store.delete_object(k)  # a delete marker: still restorable for 35 days
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
