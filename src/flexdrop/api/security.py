"""API-key authentication, role-based authorisation and per-principal rate limiting.

Keys are never stored in clear text: configuration holds SHA-256 digests
(``flexdrop create-api-key`` prints a new key and its digest). Comparison uses
``hmac.compare_digest`` to avoid timing side channels.

Roles:
* ``checkout``  - create orders, quote/record delivery preferences
* ``dispatch``  - partners, stores, optimisation, plans and plan events
* ``analyst``   - read metrics
* ``admin``     - everything, including the kill switch
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Header, Request

from flexdrop.errors import AuthenticationError, AuthorizationError, RateLimitedError

ROLES = frozenset({"checkout", "dispatch", "analyst", "admin"})


@dataclass(frozen=True)
class Principal:
    name: str
    roles: frozenset[str]


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_key() -> str:
    return "fdk_" + secrets.token_urlsafe(32)


class ApiKeyAuthenticator:
    def __init__(self, keys: dict[str, dict]):
        self._keys: list[tuple[str, Principal]] = []
        for digest, meta in keys.items():
            roles = frozenset(meta.get("roles", [])) & ROLES
            self._keys.append((digest.lower(), Principal(str(meta.get("name", digest[:8])), roles)))

    def authenticate(self, raw_key: str | None) -> Principal:
        if not raw_key:
            raise AuthenticationError("missing X-API-Key header")
        digest = hash_key(raw_key)
        match: Principal | None = None
        for known, principal in self._keys:  # constant-time comparison against every key
            if hmac.compare_digest(known, digest):
                match = principal
        if match is None:
            raise AuthenticationError("invalid API key")
        return match


class RateLimiter:
    """Token bucket per principal (in-process; use a shared store when running many instances)."""

    def __init__(self, per_minute: int):
        self.capacity = float(per_minute)
        self.rate = per_minute / 60.0
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                retry = (1.0 - tokens) / self.rate
                raise RateLimitedError("rate limit exceeded", {"retry_after_s": round(retry, 2)})
            self._buckets[key] = (tokens - 1.0, now)


def require(*roles: str) -> Callable[..., Principal]:
    wanted = frozenset(roles)
    unknown = wanted - ROLES
    if unknown:
        raise ValueError(f"unknown roles: {sorted(unknown)}")

    def dependency(request: Request, x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> Principal:
        principal = request.app.state.authenticator.authenticate(x_api_key)
        if "admin" not in principal.roles and not (principal.roles & wanted):
            raise AuthorizationError(f"requires one of roles {sorted(wanted)}")
        request.app.state.rate_limiter.check(principal.name)
        request.state.principal = principal
        return principal

    return dependency
