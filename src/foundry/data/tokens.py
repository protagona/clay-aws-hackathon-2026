"""Access tokens and the session key, read from the deployment's table (aws-deploy design ADR-9).

TOKEN#<name> items hold each token's sha256; CONFIG#session_key holds the key that signs session cookies. Both are
written only by `scripts/cloud.sh` with the operator's AWS credentials. Reads are cached for 60 s (a revoked token
stops working within a minute). If the table can't be read, the last good copy is used for up to 10 minutes, then
`Unavailable` is raised and sign-in fails closed.
"""

from __future__ import annotations

import time


class Unavailable(Exception):
    pass


class TokenStore:
    TTL_S = 60
    STALE_S = 600

    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        self._tokens: dict[str, str] | None = None
        self._key: bytes | None = None
        self._read_at = 0.0
        self._good_at = 0.0

    def _refresh(self) -> None:
        now = self.clock()
        if self._tokens is not None and now - self._read_at < self.TTL_S:
            return
        self._read_at = now
        try:
            tokens = {it["sk"]: it["sha256"] for it in self.store.query("TOKEN") if isinstance(it.get("sha256"), str)}
            cfg = self.store.get_item("CONFIG", "session_key") or {}
        except Exception:  # noqa: BLE001 (any store failure: serve the last good copy for a while, then fail closed)
            if self._tokens is None or now - self._good_at > self.STALE_S:
                raise Unavailable("the token store can't be read") from None
            return
        self._tokens, self._key, self._good_at = tokens, (cfg.get("key") or "").encode() or None, now

    def tokens(self) -> dict[str, str]:
        self._refresh()
        return dict(self._tokens or {})

    def session_key(self) -> bytes:
        self._refresh()
        if not self._key:
            raise Unavailable("no session key (run scripts/cloud.sh deploy)")
        return self._key

    def hit(self, key: str, now: float) -> int:
        """Count one attempt for a rate-limit key in the current minute; returns the count so far."""
        minute = int(now // 60)
        return self.store.incr("RATE", f"{key}#{minute}", "n", 1, ttl=(minute + 2) * 60)
