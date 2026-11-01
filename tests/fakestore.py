"""An in-memory stand-in for foundry.data.store.AwsStore with the same semantics (aws-deploy design, Tests):
conditional writes, all-or-nothing transactions with idempotency tokens, consistent reads, versioned objects with
If-None-Match, and object versions that stay readable after a delete (bucket versioning)."""

from __future__ import annotations

import copy
import itertools

from foundry.data.store import Check, Conflict, Delete, NotFound, Put, Update, apply_update, evaluate


class FakeStore:
    def __init__(self):
        self.items: dict[tuple[str, str], dict] = {}
        self.objects: dict[str, list[tuple[str, bytes | None]]] = {}  # key -> [(version_id, data or None=deleted)]
        self.tokens: dict[str, list] = {}
        self._vid = itertools.count(1)
        self.calls = {"put_object": 0, "get_object": 0, "transact": 0}
        self.fail_next_transact_after_apply = False  # simulate an ambiguous network failure (the write applied)

    # objects
    def put_object(self, key: str, data: bytes, if_none_match: bool = True) -> str:
        self.calls["put_object"] += 1
        history = self.objects.setdefault(key, [])
        if if_none_match and history and history[-1][1] is not None:
            raise Conflict(["PreconditionFailed"])
        vid = f"v{next(self._vid)}"
        history.append((vid, bytes(data)))
        return vid

    def get_object(self, key: str, version_id: str | None = None) -> bytes:
        self.calls["get_object"] += 1
        for vid, data in reversed(self.objects.get(key, [])):
            if version_id is None:
                if data is None:
                    break
                return data
            if vid == version_id and data is not None:
                return data
        raise NotFound(key)

    def delete_object(self, key: str) -> None:
        self.objects.setdefault(key, []).append((f"v{next(self._vid)}", None))  # a delete marker

    def object_size(self, key: str) -> int | None:
        try:
            return len(self.get_object(key))
        except NotFound:
            return None

    def presign_post(self, key: str, max_bytes: int, expires: int = 600) -> dict:
        return {"url": "https://fake-bucket.s3.us-west-2.amazonaws.com/", "fields": {"key": key, "max": str(max_bytes)}}

    def current_keys(self, prefix: str = "") -> list[str]:
        return sorted(k for k, h in self.objects.items() if k.startswith(prefix) and h and h[-1][1] is not None)

    # items
    def get_item(self, pk: str, sk: str) -> dict | None:
        it = self.items.get((pk, sk))
        return copy.deepcopy(it) if it is not None else None

    def query(self, pk: str, prefix: str = "", attrs: tuple = ()) -> list[dict]:
        out = [copy.deepcopy(v) for (p, s), v in sorted(self.items.items()) if p == pk and s.startswith(prefix)]
        if attrs:
            keep = {"pk", "sk", *attrs}
            out = [{k: v for k, v in it.items() if k in keep} for it in out]
        return out

    def _apply(self, a, items: dict) -> None:
        key = (a.item["pk"], a.item["sk"]) if isinstance(a, Put) else (a.pk, a.sk)
        if not evaluate(a.cond, items.get(key)):
            raise Conflict()
        if isinstance(a, Put):
            items[key] = dict(a.item)
        elif isinstance(a, Delete):
            items.pop(key, None)
        elif isinstance(a, Update):
            items[key] = apply_update(items.get(key), a)

    def write(self, action) -> None:
        self._apply(action, self.items)

    def transact(self, actions: list, token: str | None = None) -> None:
        self.calls["transact"] += 1
        if token and token in self.tokens:
            return  # idempotent retry of an applied transaction
        staged = copy.deepcopy(self.items)
        reasons = []
        for a in actions:
            key = (a.item["pk"], a.item["sk"]) if isinstance(a, Put) else (a.pk, a.sk)
            reasons.append("None" if evaluate(a.cond, staged.get(key)) else "ConditionalCheckFailed")
        if "ConditionalCheckFailed" in reasons:
            raise Conflict(reasons)
        for a in actions:
            if not isinstance(a, Check):
                self._apply(a, staged)
        self.items = staged
        if token:
            self.tokens[token] = actions
        if self.fail_next_transact_after_apply:
            self.fail_next_transact_after_apply = False
            raise TimeoutError("network failure after the write applied")
