# API specification

Base URL: `http://<host>:8000`. The interactive OpenAPI UI is at `/docs` and the schema at
`/openapi.json`; both are disabled when `FLEXDROP_ENV=production`.

## Conventions

| Topic | Rule |
|---|---|
| Auth | Header `X-API-Key`. Keys are configured as SHA-256 digests in `FLEXDROP_API_KEYS`. |
| Roles | `checkout`, `dispatch`, `analyst`, `admin` (admin may call everything) |
| Idempotency | Optional `Idempotency-Key` header on every POST. A replay returns the stored response with `Idempotent-Replayed: true`; the same key with a different body returns `409` |
| Times | ISO-8601 **with timezone** (naive datetimes are rejected with 422). Responses are in UTC |
| Errors | `{"error": {"code", "message", "request_id", "details"}}` |
| Tracing | `X-Request-ID` is echoed (or generated) on every response |
| Limits | Per-key token bucket (`FLEXDROP_RATE_LIMIT_PER_MINUTE`, 429 + `Retry-After`); body size limit (413) |
| Validation | Unknown fields are rejected (`extra=forbid`); IDs match `^[A-Za-z0-9][A-Za-z0-9_.:-]*$` (≤ 64 chars) |

Error codes: `unauthenticated` (401), `forbidden` (403), `not_found` (404), `conflict` (409),
`validation_error` / `business_rule_violation` (422), `payload_too_large` (413), `rate_limited`
(429), `internal_error` (500).

## Endpoints

| Method & path | Role | Purpose |
|---|---|---|
| `GET /health` | — | Liveness |
| `GET /ready` | — | Readiness (DB ping, loaded model versions) |
| `POST /v1/stores` | dispatch | Create or update a restaurant or Instamart store |
| `GET /v1/stores/{id}` | dispatch, checkout | Read a store |
| `POST /v1/partners` | dispatch | Create or update partner location, availability, vehicle |
| `GET /v1/partners/{id}` | dispatch | Read a partner |
| `POST /v1/orders` | checkout | Register an order; returns the standard ETA and flexible offers |
| `GET /v1/orders/{id}` | checkout, dispatch | Read an order (with offers) |
| `POST /v1/orders/{id}/cancel` | checkout, dispatch | Cancel before pickup (pending: cancelled directly; planned: removed from its plan, which is re-optimised) |
| `POST /v1/preferences` | checkout | Record the customer's explicit choice |
| `POST /v1/optimize` | dispatch | Run the optimiser and return plans |
| `GET /v1/plans/{id}` | dispatch | Current version of a plan |
| `POST /v1/plans/{id}/events` | dispatch | Execution events; triggers the CRE |
| `GET /v1/stores?zone=` | dispatch, checkout | List stores |
| `GET /v1/partners?zone=&status=` | dispatch | List partners |
| `GET /v1/orders?zone=&status=&since_hours=` | dispatch | List recent orders (without offers) |
| `GET /v1/plans?zone=&status=&since_hours=` | dispatch | List recent plans (current versions) |
| `GET /v1/reports/simulation` | analyst, dispatch | Simulation results written by `flexdrop pipeline` (`FLEXDROP_REPORTS_DIR`) |
| `GET /v1/metrics?window_hours=24` | analyst, dispatch | KPIs |
| `GET /v1/admin/kill-switch` | admin | Kill-switch state |
| `POST /v1/admin/kill-switch` | admin | Enable or disable FlexDrop |
| `GET /app/` | — | FlexDrop Studio UI (static; it authenticates with an API key) |
| `GET /metrics/prometheus` | analyst, admin (or public if `FLEXDROP_PROMETHEUS_PUBLIC=true`) | Prometheus exposition |

### POST /v1/orders

```json
{
  "id": "ORD-1001",
  "store_id": "F012",
  "customer_id": "cust-778",
  "drop": {"lat": 12.9433, "lng": 77.6250},
  "placed_at": "2026-11-09T20:41:00+05:30",
  "item_count": 2,
  "basket_value": 430,
  "handling": {"temp_classes": ["HOT"], "volume_l": 6, "weight_kg": 1.2,
               "leak_risk": false, "raw_meat": false, "sealed": true, "fragile": false},
  "customer": {"past_flex_acceptance_rate": 0.6, "is_student": true},
  "request_flex_offers": true
}
```

Response `201`:

```json
{
  "id": "ORD-1001", "status": "PENDING", "mode": "STANDARD",
  "standard_eta": "2026-11-09T15:44:12Z", "latest_delivery_at": "2026-11-09T15:44:12Z",
  "offers": [
    {"offer_id": "6f1c…", "extra_minutes": 10, "reward": 8.0, "flex_eta": "2026-11-09T15:54:12Z",
     "expires_at": "2026-11-09T15:21:00Z", "acceptance_probability": 0.61,
     "coordination_probability": 0.55, "expected_net_value": 3.4}
  ],
  "offers_unavailable_reason": null, "...": "..."
}
```

`offers_unavailable_reason` is one of `not_requested`, `flexdrop_disabled` or
`no_economically_viable_offer`. Customer IDs are pseudonymised (HMAC-SHA256) before storage.

### POST /v1/preferences

```json
{"order_id": "ORD-1001", "decision": "ACCEPT_FLEX", "offer_id": "6f1c…", "source": "checkout"}
```

`decision` is `ACCEPT_FLEX` or `STANDARD`. The response gives the approved window
(`latest_delivery_at = standard_eta + extra_minutes`). Conflicts (409): the order is not
`PENDING`, a decision already exists, the offer has expired, or the kill switch is on. A missing
`offer_id` returns 422.

### POST /v1/orders/{id}/cancel

```json
{"reason": "customer_request", "at": "2026-11-09T20:45:00+05:30"}
```

Both fields are optional. A `PENDING` order becomes `CANCELLED` immediately. A `PLANNED` order is
cancelled through its plan (`ORDER_CANCELLED` event), so the CRE re-plans the remaining stops.
Orders already picked up or delivered return 409. A pending flexible reward is `FORFEITED`.

### POST /v1/optimize

```json
{"zone": "campus", "as_of": "2026-11-09T20:50:00+05:30", "dry_run": false}
```

Response:

```json
{
  "run_id": "…", "mode": "FLEXDROP", "status": "OPTIMAL", "latency_ms": 41.2, "dry_run": false,
  "plans": [{
    "plan_id": "…", "version": 1, "status": "PROPOSED", "kind": "NEW", "partner_id": "P017",
    "order_ids": ["ORD-1001", "ORD-1004"],
    "stops": [{"seq": 0, "order_id": "ORD-1001", "type": "PICKUP", "location": {"lat": 0, "lng": 0},
               "eta": "…", "etd": "…", "wait_min": 1.2}],
    "cost_total": 92.4, "partner_pay": 86.4, "cost_breakdown": {"base": 22, "...": 0},
    "baseline_cost": 141.0, "expected_savings": 48.6,
    "flex_allocation": {"ORD-1001": 3.5, "ORD-1004": 0.0}, "lateness": {"ORD-1001": 0, "ORD-1004": 0},
    "risk_violations": 0, "start_at": "…", "end_at": "…", "distance_km": 5.1
  }],
  "held_order_ids": ["ORD-1007"], "deferred_order_ids": [], "unassigned_due_order_ids": []
}
```

`mode` is `FLEXDROP`, `FALLBACK_KILL_SWITCH` or `FALLBACK_ERROR`; in both fallback modes no plans
are returned and dispatch handles the orders normally. `kind` is `NEW`, `INSERT` (a new version
of an existing unstarted plan) or `FALLBACK` (a relaxed route for an order that is about to miss
its window; check `lateness`). With `dry_run: true`, plans are computed but nothing is persisted
(`plan_id` is null).

### POST /v1/plans/{id}/events

```json
{"type": "PREP_DELAY", "order_id": "ORD-1004", "new_ready_at": "2026-11-09T20:58:00+05:30",
 "at": "2026-11-09T20:51:00+05:30", "partner_location": {"lat": 12.94, "lng": 77.62}}
```

| `type` | Required | Effect |
|---|---|---|
| `DISPATCH_ACCEPTED` | — | `PROPOSED → ACCEPTED` |
| `DISPATCH_REJECTED` | — | Plan `REJECTED`; orders back to `PENDING`; partner `IDLE` |
| `PICKED_UP` | `order_id` | Order `PICKED_UP`; plan `IN_PROGRESS` |
| `DELIVERED` | `order_id` | Order `DELIVERED`; flexible reward `CREDITED`; plan `COMPLETED` when all orders are terminal (partner `IDLE`) |
| `ORDER_CANCELLED` | `order_id` | Allowed only before pickup; pending reward `FORFEITED`; CRE re-plans the rest |
| `PREP_DELAY` | `order_id`, `new_ready_at` | CRE: `UNCHANGED`, `RESEQUENCED` (new version), `SPLIT` (orders released), `RELEASED_ALL` |
| `PARTNER_UNAVAILABLE` | — | Un-picked orders released; on-board orders `AT_RISK`; partner `OFFLINE` |

Response: `{"plan": PlanOut, "action": "...", "released_order_ids": [...], "at_risk_order_ids": [...]}`.
Events on terminal plans (`COMPLETED`, `REJECTED`, `CANCELLED`) return 409.

### GET /v1/metrics

Returns, for the requested window:
* order counts by status, on-time rate against the approved window, average and P90
  click-to-door, deliveries with quality risk;
* orders with offers, offer acceptance rate, flexible orders;
* coordinated share, plans created and completed, expected net savings per coordinated order,
  rewards credited, planned partner pay;
* optimisation runs, fallbacks, latency P50/P95;
* kill-switch state and deployment level.

### Kill switch

`POST /v1/admin/kill-switch {"enabled": true, "reason": "incident 42"}`. The effect is immediate:
`/v1/optimize` returns `FALLBACK_KILL_SWITCH`, no new offers are quoted, and new flexible
acceptances are refused. Existing plans and standard dispatch are unaffected. The switch is also
available on the CLI (`flexdrop kill-switch on --reason ...`), and every change is audited.
