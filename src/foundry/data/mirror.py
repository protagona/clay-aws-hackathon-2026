"""The mirror: S3 "trees" (a run, a batch, a task's project files, the tool list) kept in sync with a local copy under
/tmp/state, so the engine and the existing readers keep working on plain files (aws-deploy design ADR-5).

A tree is a key prefix plus a TREE item in the table holding `version` and a `manifest` (JSON): for each relative
path, the unique object key it lives at (`<path>@v<n>.<nonce>`), its S3 VersionId, md5 and size.

    pull   read the TREE item first; GET by VersionId only what changed; delete local files the manifest dropped
    push   PUT changed files to new unique keys (If-None-Match); commit version+1 and the new manifest in one
           transaction (version check, optional lease check, idempotency token); then delete superseded keys.
           A failed or ambiguous commit re-reads the TREE: if its manifest carries our nonce the push did commit;
           otherwise our new keys are deleted and the caller gets Conflict.

Readers only fetch object versions a manifest names, so a concurrent push never shows half a tree, and deleted
keys stay readable by version for the bucket's noncurrent window (35 days).
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path

from foundry.data.store import Check, Conflict, Cond, NotFound, Update

PK = "TREE"


def prefix(tree: str) -> str:
    """The S3 prefix and the local directory (relative to the mirror root) of a tree id."""
    kind, _, name = tree.partition("#")
    if kind == "run":
        return f"runs/{name}/"
    if kind == "batch":
        return f"runs/.batches/{name}/"
    if kind == "project":
        return f"project/tasks/{name}/"
    if kind == "file":
        return "files/"
    raise ValueError(f"unknown tree {tree!r}")


def md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


class Mirror:
    def __init__(self, store, root: Path):
        self.store, self.root = store, Path(root)
        self.seen: dict[str, tuple[int, dict]] = {}  # tree -> (version, manifest) this instance last pulled or pushed

    def local_dir(self, tree: str) -> Path:
        return self.root / prefix(tree)

    def read(self, tree: str) -> tuple[int, dict, dict | None]:
        item = self.store.get_item(PK, tree)
        if item is None:
            return 0, {}, None
        return int(item.get("version", 0)), json.loads(item.get("manifest") or "{}"), item

    # --- pull -----------------------------------------------------------------------------------------------------

    def pull(self, tree: str, item: dict | None = None) -> int:
        """Bring the local copy to the store's current version. `item` may be passed from a query that already read
        the TREE item (it must include the manifest)."""
        if item is None:
            version, manifest, _ = self.read(tree)
        else:
            version, manifest = int(item.get("version", 0)), json.loads(item.get("manifest") or "{}")
        last_version, last = self.seen.get(tree, (-1, {}))
        if version == last_version:
            return version
        base = self.local_dir(tree)
        for rel, entry in manifest.items():
            path = base / rel
            if last.get(rel, {}).get("key") == entry["key"] and path.is_file():
                continue
            data = self._get(tree, entry)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.pull")
            tmp.write_bytes(data)
            tmp.replace(path)
        if base.is_dir():
            for path in [p for p in base.rglob("*") if p.is_file()]:
                rel = path.relative_to(base).as_posix()
                if rel not in manifest and not path.name.startswith("."):
                    path.unlink()
        self.seen[tree] = (version, manifest)
        return version

    def _get(self, tree: str, entry: dict) -> bytes:
        try:
            return self.store.get_object(entry["key"], entry.get("vid") or None)
        except NotFound:
            # The manifest we read was superseded and its object deleted between our read and our GET; a version-
            # pinned GET survives that, so this means a store without versioning: re-read once.
            _, manifest, _ = self.read(tree)
            for e in manifest.values():
                if e["key"] == entry["key"]:
                    return self.store.get_object(e["key"], e.get("vid") or None)
            raise

    # --- push -----------------------------------------------------------------------------------------------------

    def push(self, tree: str, files: dict[str, bytes], deleted: set[str] | frozenset = frozenset(),
             lease: tuple[str, str] | None = None, attrs: dict | None = None) -> int:
        """Write `files` (relative path -> bytes) and drop `deleted`, on top of the version this instance last saw.
        `lease` = (LOCK sort key, owner) requires that lease to still be held. `attrs` are extra TREE attributes
        (the run index's card and summary). Returns the new version; raises Conflict if someone else got there
        first (pull and redo)."""
        expected, manifest = self.seen.get(tree, (None, None))
        if expected is None:
            expected, manifest, _ = self.read(tree)
        nonce = secrets.token_hex(8)
        new_manifest = {k: v for k, v in manifest.items() if k not in deleted}
        written = []
        for rel, data in files.items():
            key = f"{prefix(tree)}{rel}@v{expected + 1}.{nonce}"
            vid = self.store.put_object(key, data, if_none_match=True)
            written.append(key)
            new_manifest[rel] = {"key": key, "vid": vid, "md5": md5(data), "size": len(data)}
        cond = Cond.absent() if expected == 0 else Cond.eq("version", expected)
        actions = [Update(PK, tree, set={"version": expected + 1, "manifest": json.dumps(new_manifest, sort_keys=True),
                                         "updated_at": int(time.time()), **(attrs or {})}, cond=cond)]
        if lease is not None:
            actions.append(Check("LOCK", lease[0], Cond.all(Cond.eq("owner", lease[1]),
                                                            Cond.gt("expires", int(time.time())))))
        try:
            self.store.transact(actions, token=f"push-{nonce}")
        except Exception as exc:
            _, current, _ = self.read(tree)
            if not any(nonce in e["key"] for e in current.values()):  # it really didn't commit
                for key in written:
                    self.store.delete_object(key)
                if isinstance(exc, Conflict):
                    raise
                raise
        superseded = {e["key"] for e in manifest.values()} - {e["key"] for e in new_manifest.values()}
        for key in superseded:
            self.store.delete_object(key)
        self.seen[tree] = (expected + 1, new_manifest)
        return expected + 1

    def push_dir(self, tree: str, lease: tuple[str, str] | None = None, attrs: dict | None = None,
                 skip=lambda rel: False) -> int | None:
        """Push whatever changed in the tree's local directory since the last version this instance saw. Dot-files
        (the engine's atomic-write temporaries, locks) are never pushed, and files that vanish mid-scan are
        skipped. Returns the new version, or None when nothing changed (and no attrs to record)."""
        base = self.local_dir(tree)
        expected, manifest = self.seen.get(tree, (None, None))
        if expected is None:
            expected, manifest, _ = self.read(tree)
            self.seen[tree] = (expected, manifest)
        files, present = {}, set()
        for path in sorted(base.rglob("*")) if base.is_dir() else []:
            rel = path.relative_to(base).as_posix()
            if any(part.startswith(".") for part in Path(rel).parts) or skip(rel):
                continue
            try:
                data = path.read_bytes()
            except (FileNotFoundError, IsADirectoryError):
                continue
            present.add(rel)
            if manifest.get(rel, {}).get("md5") != md5(data):
                files[rel] = data
        deleted = set(manifest) - present
        if not files and not deleted and not attrs:
            return None
        return self.push(tree, files, deleted, lease=lease, attrs=attrs)
