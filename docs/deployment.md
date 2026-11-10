# Deployment (no Docker)

The reference deployment runs on plain Linux VMs or bare metal:
* **uvicorn** workers managed by **systemd**,
* **nginx** as the TLS-terminating reverse proxy,
* **PostgreSQL** as the database,
* **Alembic** for schema migrations,
* **Prometheus/Grafana** for monitoring.

Templates are in `deploy/`.

## 0. Public demo in five minutes (Render, free)

To show FlexDrop Studio on a public URL without running a server yourself, the repository has a
[Render](https://render.com) blueprint, `render.yaml`. It uses native Python (no Docker) and the free plan:

1. Push the repository to GitHub.
2. Sign in to https://dashboard.render.com with GitHub. Choose **New → Blueprint**, pick the
   repository and press **Apply**.
3. The first build takes about 5 minutes. The site is then live at
   `https://<service-name>.onrender.com/app/`, and every push to `main` redeploys it.

What the blueprint does:
* **build:** `pip install -c constraints.txt -e .`, with the verified versions on Python 3.13.4;
* **start:** `python -m flexdrop studio --host 0.0.0.0 --port $PORT`;
* **health check:** `/app/`.

Things to know about this demo deployment:
* It runs **Studio mode**, the seeded demo world with synthetic data. Every visitor gets the
  session's demo admin key, and all visitors share one world, so it is a demo rather than a
  production service. A restart, which happens on every deploy, resets the world.
* The free plan sleeps after about 15 minutes without traffic. The next visit wakes it in under a
  minute. Memory use is about 200 to 250 MB, within the free 512 MB.
* The 3D view renders in the visitor's browser, so the server does no graphics work.

The rest of this document covers the production deployment.

## 1. Topology

```
            ┌──────────── TLS ────────────┐
Checkout ───►  nginx (rate limit, TLS)    ├──► flexdrop.service (uvicorn, N workers) ──► PostgreSQL (primary + replica)
Dispatch ───►                              │            │
            └──────────────────────────────┘            └── /metrics/prometheus ◄── Prometheus ──► Grafana / alerts
```

## 2. Host preparation

```bash
sudo useradd --system --home /opt/flexdrop --shell /usr/sbin/nologin flexdrop
sudo mkdir -p /opt/flexdrop /etc/flexdrop && sudo chown flexdrop: /opt/flexdrop
sudo -u flexdrop python3.11 -m venv /opt/flexdrop/.venv
sudo -u flexdrop /opt/flexdrop/.venv/bin/pip install -c /path/to/flexdrop/constraints.txt "/path/to/flexdrop[postgres]"
sudo cp -r models /opt/flexdrop/models          # trained artefacts (or run `flexdrop pipeline`)
sudo cp deploy/env.production.example /etc/flexdrop/flexdrop.env && sudo chmod 600 /etc/flexdrop/flexdrop.env
```

Fill in `/etc/flexdrop/flexdrop.env`:
* the database URL;
* a 32+ character `FLEXDROP_PSEUDONYMIZATION_SECRET` (keep it in a secret manager);
* the API-key digests. Create one key per calling system (checkout, dispatch, analytics,
  operations admin) with `flexdrop create-api-key`.

## 3. Database

```sql
CREATE ROLE flexdrop LOGIN PASSWORD '***';
CREATE DATABASE flexdrop OWNER flexdrop;
```

```bash
sudo -u flexdrop env $(cat /etc/flexdrop/flexdrop.env | xargs) /opt/flexdrop/.venv/bin/alembic -c /path/to/alembic.ini upgrade head
```

Production never auto-creates the schema (`AUTO_CREATE_SCHEMA` is ignored when `ENV=production`).

## 4. Service

```bash
sudo cp deploy/systemd/flexdrop.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now flexdrop
curl -fsS http://127.0.0.1:8000/ready
```

Retention runs as a systemd timer (`deploy/systemd/flexdrop-purge.{service,timer}`, daily).

## 5. nginx

`deploy/nginx/flexdrop.conf` does the following:
* terminates TLS;
* forwards `X-Request-ID`;
* applies a coarse per-IP rate limit (the service also limits per API key);
* caps body size at 1 MB;
* restricts `/metrics/prometheus` to the monitoring network.

## 6. Scaling and concurrency

* **Workers.** The API is stateless; scale `--workers` and the number of hosts. Optimisation runs
  are serialised per process and protected across processes by optimistic locking (a conflicting
  run gets 409 and dispatch retries next epoch).
* **Zones.** Dispatch calls `/v1/optimize` per zone (for example per campus or cluster). Configure
  one dispatch caller per zone so that zones optimise in parallel without contention.
* **Rate limiting.** The built-in limiter is per process. Behind several workers, rely on nginx
  `limit_req` for global limits or add a shared-store limiter.
* **Latency budget.** P95 is about 50–110 ms on the simulated loads. `FLEXDROP_CPSAT_TIME_LIMIT_S`
  caps the worst case, and the greedy solution is always available.

## 7. Monitoring and alerting

Prometheus metrics:
* `flexdrop_http_requests_total{method,route,status}`
* `flexdrop_http_request_seconds`
* `flexdrop_optimization_seconds`

Business KPIs come from `GET /v1/metrics`. Suggested alerts:

| Alert | Condition |
|---|---|
| Optimiser fallback | `optimization_fallbacks` increases (error mode) |
| Latency | P95 `flexdrop_optimization_seconds` > 2 s for 10 min |
| Service reliability | `on_time_rate` (window) drops > 1 pp below the control group |
| Food safety | `quality_risk_deliveries` > 0 |
| Error rate | 5xx > 1% |

## 8. Rollout plan (proposal §14)

1. **Shadow mode.** Dispatch calls `/v1/optimize` with `dry_run: true` and compares the plans with
   its own decisions. Nothing is executed.
2. **Level 1 pilot.** One zone, same-category coordination, a small opt-in customer group, with a
   control group on standard dispatch.
3. **Acceptance gates:**
   * ≥ 5% fulfilment-cost reduction on eligible orders;
   * ≤ 1 pp on-time decline;
   * zero food-safety violations;
   * no reduction in protected partner earnings;
   * optimisation latency < 5 s.
4. **Levels 2 and 3** (`FLEXDROP_DEPLOYMENT_LEVEL`): enable only after the gates pass.

## 9. Operations runbook

| Situation | Action |
|---|---|
| Any incident | `POST /v1/admin/kill-switch {"enabled": true, "reason": "..."}` or `flexdrop kill-switch on --reason ...`. The effect is immediate; standard dispatch continues |
| Solver failures | The service already falls back per request (`mode=FALLBACK_ERROR`). Check the logs (`request_id`) |
| Bad model release | Restore the previous `models/` directory and restart. If artefacts fail to load, analytic priors are used automatically |
| Database migration | `alembic upgrade head` before deploying new code; `alembic downgrade -1` to roll back |
| Data deletion request | `flexdrop purge --days N`. Customer IDs are pseudonymised; locate rows by recomputing the HMAC |
