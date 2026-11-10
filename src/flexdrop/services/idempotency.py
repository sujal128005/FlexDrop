"""Idempotent POST handling (``Idempotency-Key`` header).

The first successful response for (actor, key) is stored; replays with the same request body
return the stored response, replays with a different body are rejected with 409.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import IntegrityError

from flexdrop.db.models import IdempotencyRow
from flexdrop.db.session import Database
from flexdrop.errors import BusinessRuleError, ConflictError

MAX_KEY_LENGTH = 128


def request_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def run_idempotent(
    db: Database,
    actor: str,
    key: str | None,
    method: str,
    path: str,
    body: bytes,
    action: Callable[[], tuple[int, Any]],
) -> tuple[int, Any, bool]:
    """Execute ``action`` at most once per idempotency key. Returns (status, body, replayed)."""
    if key is None:
        status, payload = action()
        return status, payload, False
    if not key or len(key) > MAX_KEY_LENGTH:
        raise BusinessRuleError(f"Idempotency-Key must be 1..{MAX_KEY_LENGTH} characters")
    digest = request_hash(body)
    with db.session() as s:
        existing = s.get(IdempotencyRow, (actor, key))
        if existing is not None:
            if existing.request_hash != digest or existing.method != method or existing.path != path:
                raise ConflictError("Idempotency-Key reused with a different request")
            return existing.status_code, existing.response_body, True
    status, payload = action()
    try:
        with db.session() as s:
            s.add(
                IdempotencyRow(
                    actor=actor,
                    key=key,
                    method=method,
                    path=path,
                    request_hash=digest,
                    status_code=status,
                    response_body=payload,
                )
            )
    except IntegrityError:
        # A concurrent request with the same key won the race; its record is authoritative.
        with db.session() as s:
            existing = s.get(IdempotencyRow, (actor, key))
            if existing is not None:
                return existing.status_code, existing.response_body, True
    return status, payload, False
