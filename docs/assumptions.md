# Assumptions and engineering decisions

The proposal left several details open. Every decision taken to deliver a complete system is
listed here so it can be reviewed and changed.

## Business and operational assumptions

| # | Assumption | Value / rule | Where to change |
|---|---|---|---|
| A1 | All costs are hypothetical, not Swiggy figures | base ₹22/route, ₹7/km, ₹0.8/engaged min, ₹12 per extra drop, ₹3 platform overhead, ₹1.5 coordination overhead | `CostParams` |
| A2 | Partner earnings floor | ₹140 per engaged hour (top-up when route pay falls below it) | `CostParams.min_partner_hourly_earnings` |
| A3 | Rewards are paid on delivery even if no coordination happened | priced in through the coordination probability `q` | `engines/incentive.py` |
| A4 | Reward bounds | ₹5–30, at most 60% of expected attributable savings, ≥ ₹1 expected margin | `FlexParams` |
| A5 | Wait options | 5 / 10 / 15 minutes | `FlexParams.wait_options_min` |
| A6 | Food-age limits from readiness | hot 40, chilled 60, frozen 45, ambient 180 min; 5 min readiness uncertainty | `QualityParams` |
| A7 | Partner protection | ≤ 5 min predicted wait at a store (just-in-time dispatch), routes ≤ 80 min, ≤ 2 route changes | `OptimizerParams` |
| A8 | Standard promise | `max(ready, placed + approach) + pickup service + trip + 8 min buffer`; typical approach 2 km | `PromiseParams`, `TravelParams` |
| A9 | Road distance | 1.35 × great-circle distance (no road graph) | `TravelParams.road_factor` |
| A10 | Cancellations | Accepted only before pickup; a pending reward is forfeited | `services/dispatch.py` |
| A11 | Partner vehicles | Default bag 45 L / 15 kg / 2 insulated compartments; 10% of simulated partners have 30 L / 10 kg / 1 compartment | `Vehicle`, scenario |
| A12 | Delivery-time measure | Arrival at the drop point (handover starts) | `routing.py` |
| A13 | Time zone for time-of-day features | IST (UTC+5:30) | `TravelParams.tz_offset_min` |
| A14 | Deployment levels | L1 same-category groups ≤ 3; L2 cross-category; L3 groups ≤ 4 + insertion into unstarted routes | `EngineConfig.for_level` |

## Simulation assumptions

| # | Assumption |
|---|---|
| S1 | Campus: 35 restaurants and 2 Instamart stores within 0.6–3 km of a campus; 10 hostel gates; 12% of drops off campus; 90% student customers; 70 partners; evening demand of 1.3–2.6 orders/min |
| S2 | City: 60 restaurants and 4 stores in a 3.5 km area; 400 dispersed drop points; 15% students; 76 partners |
| S3 | Ground-truth preparation: store base time × item factor + load + an evening peak bump, with lognormal noise (σ = 0.18 food, 0.20 grocery) |
| S4 | Ground-truth travel: 19 km/h × time-of-day congestion, lognormal noise (σ = 0.15) |
| S5 | Customer utility for an offer: `−1.8 + 0.22·r − 0.16·m·(0.5 + urgency) + 1.6·price_sensitivity + 0.4·student`; multinomial-logit choice between tiers and standard |
| S6 | 2% of orders cancel 1–12 min after placement; restaurants signal delays > 2 min about 3 min before the predicted ready time |
| S7 | Idle partners reposition (unpaid) to the nearest demand hub; partner drop-out rate 1%/hour |
| S8 | Decision epoch 1 min; CP-SAT time limit 1 s with a single worker in simulation (determinism) |
| S9 | Models are trained on a scenario with a different seed from the evaluation scenarios (no leakage) |

## Technology decisions

| Proposed | Delivered | Rationale and upgrade path |
|---|---|---|
| FastAPI, PostgreSQL, OR-Tools CP-SAT, scikit-learn | ✅ as proposed | — |
| XGBoost | scikit-learn `HistGradientBoostingRegressor` | Same model family (histogram gradient boosting) and no extra native dependency. Swap in via `LearnedPrepTime`/`LearnedTravelTime` |
| Kafka | Synchronous REST events (`/v1/plans/{id}/events`) | Events need per-plan ordering and synchronous CRE answers; a Kafka consumer can call the same service functions |
| Redis | Not needed (state in PostgreSQL, idempotency in DB) | Add a shared-store rate limiter for multi-instance global limits |
| PostGIS / GeoPandas | Haversine + road factor + bounded candidate search | Areas are small (≤ 6 km); PostGIS radius queries are the scaling path for density features |
| SimPy | A custom discrete-event simulator (heap-based) | Simpler, dependency-free, deterministic; supports insertion and CRE semantics directly |
| Grafana | Prometheus exposition + `/v1/metrics` | Dashboards are a deployment concern |
| React / Next.js frontend | **FlexDrop Studio**: plain HTML/CSS/JS modules served by the API at `/app/`, bundled Leaflet map | No build toolchain needed, so it runs from one command in Colab, WSL or Linux. The same API could back a React app later |
| Real map of IIITDM Kurnool | **Stylised site plan** (`studio.py`) built from public descriptions and photos: one main gate on NH 40, clock tower roundabout, academic area, hostels behind it, Jagannathagattu hills, Kurnool city to the north | Studio only. City distance compressed from about 12 km to about 3 km; restaurant names are real Kurnool names with the last word changed; building positions and sizes are approximate. Simulation results do not depend on it |
| Docker | **Not used** (per requirements) | systemd + nginx + venv deployment ([deployment.md](deployment.md)) |

## Algorithmic decisions

| Decision | Why |
|---|---|
| Column generation + set packing instead of one monolithic MIP | Exact sequencing per small group plus a global matching is fast, explainable and bounded. A full PDPTW MIP over all orders and partners does not fit the per-epoch latency budget |
| Exact branch-and-bound sequencing (≤ 4 orders) | Optimal and verifiable for the group sizes that are operationally sensible for two-wheelers |
| Rolling horizon with holds instead of stochastic look-ahead | Holds are bounded by each customer's own budget and dispatch deadline, so there is no speculative risk to commitments |
| Risk penalty from P90 replays | Prefers robust sequences without needing a full stochastic model |
| Fallback columns | Guarantees no order is stranded; lateness is explicit and reported |
| Assignment bonus β = 250 | Larger than any feasible route cost, so the optimiser always serves an order when a feasible column exists |
| Hold safety margin of 5 min | Chosen by simulation (3 / 4 / 5 / 6 min tested). 5 min balanced savings and on-time rate across both scenarios ([simulation.md](simulation.md#sensitivity)) |
| Logistic acceptance model | Monotonicity guarantees and calibrated probabilities for expected-value pricing |

## Known limitations

* All results come from synthetic data. Real gains depend on actual density, acceptance and cost
  structure; this is exactly what the shadow mode and the pilot are for.
* Travel times use a road factor, not a road network. Integrating Swiggy's ETA service would
  replace `TravelTimeModel`.
* FlexDrop lowers total partner pay because there are fewer, shorter routes. Earnings per engaged
  hour stay at the floor or above, but fleet sizing and incentive design must account for this.
  Part of the savings could be shared with partners.
* The in-process rate limiter and optimisation lock are per worker (see deployment notes).
