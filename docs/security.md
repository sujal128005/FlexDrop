# Security and privacy

| Threat or requirement | Control | Where |
|---|---|---|
| Unauthenticated access | API key required on every business endpoint | `api/security.py` |
| Key theft from configuration | Only SHA-256 digests are stored; keys are shown once at creation | `flexdrop create-api-key` |
| Timing attacks on key comparison | `hmac.compare_digest` against every configured key | `ApiKeyAuthenticator` |
| Privilege misuse | Roles `checkout`, `dispatch`, `analyst`, `admin`, checked per endpoint | `require(...)` |
| Abuse / DoS | Per-key token bucket (429 + `Retry-After`), body-size limit (413), nginx `limit_req` | middleware, nginx |
| Injection | SQLAlchemy parameterised queries only; strict pydantic schemas (`extra=forbid`, ID regex, numeric ranges, timezone-aware datetimes) | `schemas.py` |
| Replayed or duplicated requests | `Idempotency-Key` with request hashing; unique constraints | `services/idempotency.py` |
| Inconsistent concurrent writes | Optimistic locking on orders and partners; one DB transaction per use case | `db/models.py`, services |
| Unapproved delivery-window changes | Windows change only through recorded consent; plans are validated against budgets before persisting; CRE re-plans are strict | `services/orders.py`, `services/dispatch.py::_validate` |
| Personal data | Customer IDs pseudonymised with HMAC-SHA256 (secret ≥ 32 chars, enforced in production); retention purge; no names or phone numbers accepted | `services/common.py`, `services/privacy.py` |
| Information leakage in errors | Uniform error body; stack traces only in server logs; no internal details in 500 responses | `api/errors.py` |
| Browser-side attacks | `X-Content-Type-Options`, `X-Frame-Options: DENY`, restrictive CSP, `Cache-Control: no-store`, `Referrer-Policy` | middleware |
| Untrusted model artefacts | Artefacts are loaded only from the operator-controlled `FLEXDROP_MODEL_DIR` with type checks (joblib/pickle must never load user-supplied files) | `ml/registry.py` |
| Accountability | Append-only audit log with actor and request ID for consent, pricing, plans, events, kill switch | `audit_logs` |
| Misconfigured production | Settings validation refuses to start in production without keys or a strong secret; OpenAPI UI disabled | `config.py` |
| Operational safety | Kill switch; automatic fallback to standard dispatch on optimiser error | `services/dispatch.py` |

Recommended in production, outside the scope of this code:
* TLS everywhere (nginx config provided);
* secrets from a secret manager;
* network policies so only the checkout, dispatch and monitoring systems can reach the service;
* database encryption at rest;
* key rotation by adding a new digest, migrating callers, then removing the old digest.
