"""Application exceptions mapped to HTTP problem responses by the API layer."""

from __future__ import annotations

from typing import Any


class FlexDropError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(FlexDropError):
    status_code = 404
    code = "not_found"


class ConflictError(FlexDropError):
    status_code = 409
    code = "conflict"


class BusinessRuleError(FlexDropError):
    status_code = 422
    code = "business_rule_violation"


class AuthenticationError(FlexDropError):
    status_code = 401
    code = "unauthenticated"


class AuthorizationError(FlexDropError):
    status_code = 403
    code = "forbidden"


class RateLimitedError(FlexDropError):
    status_code = 429
    code = "rate_limited"


class ServiceUnavailableError(FlexDropError):
    status_code = 503
    code = "service_unavailable"
