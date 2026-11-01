"""Who may use a deployment, with no AWS identity (aws-deploy spec A1–A2, A6; design ADR-9).

- A token (`fdy_` + 32 random bytes) is checked against its stored sha256 in constant time.
- A browser signs in once and gets a session cookie `<name>.<expiry>.<HMAC(key, name|expiry|token_sha256)>`. It
  is bound to the token's hash, so revoking the token (or re-adding the name with a new one) ends its sessions.
- The CSRF token for a cookie session is derived from the cookie (HMAC), so any instance can check it; requests
  authenticated by the X-Foundry-Token header carry no ambient credential and need none.
- Failed sign-ins and wrong tokens are limited to 10 a minute per client address (IPv6 by /64).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import time

from foundry.data.tokens import TokenStore, Unavailable
from foundry.services.errors import ServiceError

COOKIE = "fdy_session"
SESSION_S = 12 * 3600
ATTEMPTS_PER_MINUTE = 10


def sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def client_key(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip or "?"
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False).network_address) + "/64"
    return str(addr)


class Access:
    def __init__(self, tokens: TokenStore, clock=time.time):
        self.tokens, self.clock = tokens, clock

    def _guarded(self, fn, *args):
        try:
            return fn(*args)
        except Unavailable:
            raise ServiceError(500, "Sign-in is unavailable; try again shortly") from None

    def token_name(self, token: str) -> str | None:
        digest = sha256(token)
        for name, stored in self._guarded(self.tokens.tokens).items():
            if hmac.compare_digest(digest, stored):
                return name
        return None

    def limit(self, ip: str) -> None:
        if self._guarded(self.tokens.hit, client_key(ip), self.clock()) > ATTEMPTS_PER_MINUTE:
            raise ServiceError(429, "Too many attempts; wait a minute")

    def _mac(self, name: str, expiry: int, token_hash: str) -> str:
        key = self._guarded(self.tokens.session_key)
        return _b64(hmac.new(key, f"{name}|{expiry}|{token_hash}".encode(), hashlib.sha256).digest())

    def new_session(self, name: str) -> str:
        expiry = int(self.clock()) + SESSION_S
        return f"{name}.{expiry}.{self._mac(name, expiry, self._guarded(self.tokens.tokens)[name])}"

    def session_name(self, cookie: str) -> str | None:
        try:
            name, expiry_s, mac = cookie.split(".", 2)
            expiry = int(expiry_s)
        except ValueError:
            return None
        token_hash = self._guarded(self.tokens.tokens).get(name)
        if token_hash is None or expiry < self.clock():
            return None
        return name if hmac.compare_digest(mac, self._mac(name, expiry, token_hash)) else None

    def csrf_for(self, cookie: str) -> str:
        key = self._guarded(self.tokens.session_key)
        return _b64(hmac.new(key, f"csrf|{cookie}".encode(), hashlib.sha256).digest())
