# Algorithms

This document explains *how* each module works and *why* it was designed that way. The formal
model is in [math_model.md](math_model.md).

## 1. Customer Flexibility Intelligence (CFI)

**Goal.** Estimate how likely a customer is to accept "wait *m* more minutes for ₹*r*", and make
sure no window ever changes without the customer's explicit acceptance.

* **Model.** Logistic regression on reward, extra minutes, the customer's past flexible-offer
  acceptance rate, student flag, basket value and time of day (`ml/models.py::LearnedAcceptance`).
  A linear logit was chosen deliberately: a higher reward can never lower the predicted
  acceptance, and a longer wait can never raise it. Training checks the sign of both
  coefficients, and the test suite asserts them.
* **Consent.** `POST /v1/preferences` is the only path that sets `mode=FLEX` and `max_extra_wait`.
  The request must reference an offer for that order that has not expired; the kill switch must be
  off; and the order must still be `PENDING`. Each order allows exactly one decision, enforced by
  a unique constraint. Every decision is written to the audit log.

## 2. Order Compatibility Intelligence (OCI)

Two stages (`engines/compatibility.py`):

1. **Hard rules** that reject a combination however profitable it looks:

   | Rule | Reason code |
   |---|---|
   | Food and Instamart combined at Level 1 | `cross_category_disabled` |
   | Hot and chilled/frozen without 2 insulated compartments | `thermal_segregation_unavailable` |
   | Raw meat with ready-to-eat food unless both sealed | `raw_meat_contamination` |
   | Unsealed leaky item with food | `leak_risk` |
   | Fragile item with a heavy item (> 6 kg) | `fragile_crush_risk` |
   | Volume or weight above the partner's vehicle | `volume_capacity`, `weight_capacity` |
   | Pickups > 2 km apart, drops > 2.5 km apart | `pickup_too_far`, `drop_too_far` |
   | Ready times > 12 min apart | `ready_gap_too_large` |
   | Combined path > 1.6 × the longer solo trip | `detour_too_large` |

2. **Score** (0–1) from distance saving, ready-time alignment and direction similarity. The score
   only ranks neighbours during candidate generation; the optimiser makes the economic decision.

Group feasibility requires every pair to be compatible and the group-level handling and capacity
checks to pass for the *specific* partner's vehicle.

## 3. Dynamic Coordination Optimizer (DCO)

Runs once per decision epoch (default: 1 minute; the API runs it whenever dispatch calls
`/v1/optimize`).

1. **Classify** each pending order using the flexibility-budget deadlines: due, holdable, or
   neither.
2. **Prime the travel oracle.** One batched ML call computes all point pairs.
3. **Generate groups.** Cheap geometric prefilters run first, then OCI pair checks. Groups are:
   singletons, pairs from the top-k neighbours, then triples and quads whose pairs are all
   compatible. The group budget is bounded.
4. **Price columns.** Each group is sequenced exactly for each of its nearest partners. The column
   value is `β·|G| − J`.
5. **Insertion columns** (Level 3). A pending order may join a route whose partner has not yet
   reached the first stop, if the marginal objective beats a solo dispatch by a margin.
6. **Fallback columns.** A due order with no feasible strict column gets relaxed singleton columns
   (lateness penalised), so no order is ever stranded.
7. **Select** with CP-SAT weighted set packing, using the greedy solution as hint and fallback.
8. **Report** routes, held orders (waiting for coordination), deferred orders (not yet
   dispatchable, for example food not ready) and unassigned due orders (no partner available).

**Just-in-time dispatch.** Partners are not dispatched if they would wait more than 5 minutes at a
store. This protects partner time; without it, simulated fleets lost about 30% of capacity to
waiting.

**Risk-aware sequencing.** Each candidate sequence is replayed under pessimistic times (P90
preparation, travel × 1.2). Sequences that would break a commitment or a food-age limit in that
replay are penalised, which favours robust plans over fragile ones.

## 4. Route sequencing

Exact depth-first branch-and-bound over precedence-feasible stop orders (`engines/routing.py`).
Engineering details:

* Index-based travel/distance matrices per call; group rows are cached across candidate partners
  (about 4× faster).
* An inline cost bound that is identical to `CostModel.route_cost`.
* Early infeasibility pruning: if picking up order *i* now cannot get it delivered by its
  deadline even on a direct trip, the branch is cut.
* A relaxed mode (`enforce=False`) for rescue and fallback routes, which minimises lateness.
* `evaluate_sequence` evaluates a *given* sequence. The CRE stability rule uses it, and the tests
  use it for brute-force verification.

## 5. Continuous Re-optimisation Engine (CRE)

`engines/reoptimizer.py`, invoked by the API on plan events and by the simulator on simulated
events.

* **Anchor.** The stop the partner is heading to stays fixed. Re-planning starts from the
  partner's projected departure from that stop. In the API, a plan that has not started is
  anchored at its planned start time.
* **Stability.** The current sequence is kept unless a new one is better by more than
  `insertion_min_gain`. This avoids churn for partners.
* **Commitment protection.** Re-plans are strict. If they cannot be met, un-picked orders are
  released back to the pool (largest feasible subset kept). On-board orders are never released;
  if even they cannot be delivered on time, the route minimises lateness and the orders are
  flagged `AT_RISK` for operations.
* **Partner unavailable.** Un-picked orders are released immediately and on-board orders are
  flagged for rescue.

## 6. Economic Incentive Optimizer (EIO)

`engines/incentive.py`, called at checkout.

1. Compute the attributable savings `S̄` from the solo baseline and the marginal cost of joining a
   group. The saving is split equally between the two orders of a pair.
2. Compute the local density of open orders and predict the coordination probability `q(m)`.
3. Score the full grid of (wait option × reward) with one vectorised acceptance-model call.
4. Pick the reward that maximises `p·(q·S̄ − r)` under the caps: the reward is at most 60% of the
   expected savings, and the expected margin is at least ₹1.
5. Apply the food-quality guard to remove wait options that cannot be honoured without breaking
   the age limit.

Result: in quiet areas no offer is shown (`no_economically_viable_offer`); in dense hostel
clusters the offers are larger. Rewards adapt to each customer's acceptance profile.

## 7. Analytic priors (cold start)

When no trained artefact is present, or one fails to load, documented formulas are used:

| Model | Prior |
|---|---|
| Prep | food: `base + 0.5·(items−1) + 0.4·load`; grocery: `3 + 0.35·items + 0.3·load`; P90 = `1.3·mean + 2` |
| Travel | `1 + km / (20 km/h · congestion(hour)) · 60` |
| Acceptance | `σ(−1.5 + 0.2r − 0.15m + 2(past − 0.5) + 0.3·student)` |
| Coordination | `1 − exp(−(0.08·n_both + 0.02·n_pickup + 0.01·n_drop)(1 + m/10))`, clipped to [0.02, 0.95] |

## 8. Learning loop

`flexdrop pipeline`:
1. Generate historical prep, trip and offer data from a *training* scenario (a different seed
   from evaluation).
2. Train the prep, travel and acceptance models.
3. Run FlexDrop on calibration seeds and log `(density features, m) → coordinated?` samples.
4. Train the coordination model and retrain everything.
5. Evaluate all policies on held-out evaluation scenarios.

The coordination model therefore learns from the system's *own* outcomes, which is the proposal's
"monitoring and feedback layer".
