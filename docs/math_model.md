# Mathematical model

Notation: minutes for time, km for distance, INR for money. All parameter defaults are in
`src/flexdrop/config.py`.

## 1. Sets and data

| Symbol | Meaning |
|---|---|
| `I` | pending orders in the current decision epoch |
| `K` | idle partners (available within the epoch) |
| `R` | active routes that have not started (Level 3 insertion) |
| `p_i, d_i` | pickup (store) and drop location of order `i` |
| `t_i^place` | placement time |
| `r̂_i, r̂_i^90` | predicted ready time (mean, P90), from the prep-time model |
| `τ(a,b)` | predicted travel minutes, from the travel model, frozen at the epoch time |
| `δ(a,b)` | road distance = haversine × road factor (1.35) |
| `P_i` | standard promise (see §3) |
| `w_i` | **approved** extra minutes (0 for standard delivery); only set by recorded consent |
| `L_i = P_i + w_i` | hard delivery commitment |
| `A_i` | food-age limit from predicted readiness = `min_temp class(age_max) − u` with readiness uncertainty `u = 5` |
| `σ_p, σ_d` | service time at pickup (2.0) and drop (2.5) |

## 2. Fulfilment cost of a route

For a route with road distance `D` (approach included), engaged minutes `T` (approach, waiting,
service, travel) and `n` orders:

```
pay(D, T, n)   = F_base + c_km·D + c_min·T + F_drop·(n − 1)
topup(D, T, n) = max(0, E_min·T/60 − pay)                     (partner earnings floor)
C(D, T, n)     = pay + topup + c_coord·(n − 1) + F_overhead
```

Defaults: `F_base = 22`, `c_km = 7`, `c_min = 0.8`, `F_drop = 12`, `E_min = 140 ₹/h`,
`c_coord = 1.5`, `F_overhead = 3`.

**Solo baseline** of order `i` (used for savings attribution):
`B_i = C(δ_a + δ(p_i,d_i), τ_a + σ_p + τ(p_i,d_i) + σ_d, 1)`, with typical approach `δ_a = 2 km`.

## 3. Standard promise

```
P_i = max(t_i^place + prep̂_i, t_i^place + τ_a) + σ_p + τ(p_i, d_i) + buffer      (buffer = 8)
```

## 4. Flexibility budget allocation (core innovation)

For a route visiting order `i` with delivery time `a_i`:

```
s_i = clip(a_i − P_i, 0, w_i)           allocated flexibility
0 ≤ s_i ≤ w_i                           individual, non-transferable budget
a_i ≤ P_i + w_i                         hard commitment (no unapproved extension)
```

The optimiser minimises `λ·Σ s_i` (λ = 0.5 ₹/min) together with cost. Among equally cheap plans it
therefore uses the **least** flexibility. In simulation only about 14–19% of approved minutes are
used (`flex_utilisation`).

**Dispatch deadline and holds.** For each order:

```
D_i = min(L_i, r̂_i + A_i) − τ(p_i, d_i) − σ_p − τ_a − h        (h = hold safety, 5 min)
due_i      ⇔ now + Δ ≥ D_i                                     (Δ = epoch length, 1 min)
holdable_i ⇔ FLEX ∧ w_i > 0 ∧ ¬due_i
```

A holdable order may only be dispatched in a group of two or more orders. It waits in the pool for
compatible orders until it becomes due, and then it can go solo. This is the rolling-horizon
mechanism that turns flexibility into coordination.

## 5. Route sequencing (single partner, group G, |G| ≤ 4)

Decision: a precedence-feasible sequence of stops. Times propagate as follows:

```
pickup i:  arr = t + τ(loc, p_i);  wait_i = max(0, r̂_i − arr);  dep = arr + wait_i + σ_p
drop i:    a_i = t + τ(loc, d_i);   dep = a_i + σ_d
```

Hard constraints (strict mode):

```
pickup(i) before drop(i)
wait_i ≤ W_max (5)                      just-in-time dispatch, partner protection
a_i ≤ L_i                               commitment
a_i − r̂_i ≤ A_i                         food quality
end − start ≤ T_max (80)                partner workload
Σ vol_i ≤ V_k,  Σ kg_i ≤ M_k            capacity (checked by OCI)
```

Objective (minimise):

```
J = C(D, T, |G|) + λ·Σ s_i + ρ·V_risk (+ μ·Σ late_i in relaxed mode)
```

`V_risk` counts orders that would break `L_i` or the age limit if the sequence were replayed with
P90 readiness and travel × 1.2 (ρ = 25). Solved **exactly** by depth-first branch-and-bound:
`C` is monotone along a partial path, so the partial objective is a valid lower bound. In the
worst case there are 8!/2⁴ = 2520 leaves for 4 orders. Exactness is checked in
`tests/unit/test_routing_flexibility.py` against brute-force enumeration.

## 6. Column generation and set packing (DCO)

**Columns.** A column `c = (G, k)` is a group of orders served by partner `k`, with its optimal
sequence. Its value is:

```
v_c = β·|G_new| − J_c                   β = assignment bonus (250)
```

Columns with `v_c ≤ 0` are dropped. Holdable orders do not get singleton columns. Insertion
columns (Level 3) add order `j` to an unstarted route `r`:
`v = β − (J_{r∪j} − J_r)`, and they require `J_{r∪j} − J_r ≤ B_j − g_min`. Fallback columns are
relaxed singletons for due orders that have no strict column, with `v = max(1, β − J^relaxed)`.

**Selection** (weighted set packing, solved with OR-Tools CP-SAT):

```
max   Σ_c v_c · x_c
s.t.  Σ_{c ∋ i} x_c ≤ 1          ∀ i ∈ I          (each order at most once)
      Σ_{c ∋ k} x_c ≤ 1          ∀ k ∈ K          (each partner at most one new route)
      Σ_{c ∋ r} x_c ≤ 1          ∀ r ∈ R          (at most one insertion per route per epoch)
      x_c ∈ {0, 1}
```

Values are scaled to integers (paise). The greedy solution (value-descending, conflict-free) is
passed as a hint and used as the fallback. CP-SAT optimality is checked against brute force in
`tests/optimization/test_optimizer.py`.

**Candidate generation.**
* Pairs: the top-k (6) compatible neighbours of each order, ranked by compatibility score.
* Triples and quads: extend groups where every pair is compatible.
* Overall budget: `max_candidate_groups` (2500).
* Partners per group: the nearest 4 for groups of up to 2 orders, 3 for larger groups, within 6 km.

## 7. Compatibility score (ranking only)

```
saving   = (δ_a + d_i + d_j − L_ij) / (2δ_a + d_i + d_j)        L_ij = best precedence-feasible pair path
gap      = 1 − |r̂_i − r̂_j| / G_max
score_ij = 0.6·max(0, saving) + 0.25·gap + 0.15·(cosθ_ij + 1)/2
```

Hard rules reject a pair before scoring. They are: thermal segregation, raw-meat contamination,
unsealed leaks next to food, fragile items with heavy items, capacity, category policy (Level 1),
pickup ≤ 2 km, drops ≤ 2.5 km, ready gap ≤ 12 min, and `L_ij ≤ 1.6·max(d_i, d_j, 1)`.

## 8. Incentive optimisation (EIO)

For each wait option `m ∈ {5, 10, 15}` that respects the quality guard
`P_i + m ≤ r̂_i + A_i − 5`:

```
S̄      = max(0, (B_i − C_marg)/2)                        attributable savings per coordinated order
C_marg = F_drop + c_coord + c_km·0.6 + c_min·4            marginal cost of adding an order
q(m)   = P(coordinated | m, density)                      coordination model
p(m,r) = P(accept | m, r, customer)                       acceptance model

r*(m) = argmax_r  p(m,r) · (q(m)·S̄ − r)
        s.t.  r ∈ {5, 6, …, 30}
              r ≤ 0.6 · q(m)·S̄                            never give away the savings
              q(m)·S̄ − r ≥ 1                              viability margin
```

An option is shown only if its expected value is positive. The reward is paid on delivery
whether or not coordination happens, so the risk of no coordination is priced in through `q(m)`.

## 9. Re-optimisation (CRE)

Given the partner's projected state `(location, time)` after the stop in flight, the remaining
orders `O` and the on-board subset `B ⊆ O`:

1. If the full set `O` is feasible, keep the current sequence when
   `J_current ≤ J_best + g_min` (stability rule). Otherwise resequence.
2. If `O` is not feasible, find the largest subset `S` of not-yet-picked orders such that `B ∪ S`
   is feasible, breaking ties by minimum `J`. Release `O \ (B ∪ S)` to the pool.
3. If `B` alone is infeasible, sequence it in relaxed mode to minimise lateness and flag those
   orders as `AT_RISK`. On-board orders are never released.

## 10. Prediction models

| Model | Target | Features | Algorithm |
|---|---|---|---|
| Prep time | minutes to ready (mean, P90) | is_food, store base prep, items, hour (sin/cos), weekend, store load (orders in last 20 min) | HistGradientBoosting (squared error; quantile 0.9) |
| Travel | leg minutes | road km, hour (sin/cos), weekend | HistGradientBoosting |
| Acceptance | P(accept offer) | reward, extra minutes, past acceptance rate, student, basket, hour | Logistic regression (linear logit ⇒ monotone in reward and wait) |
| Coordination | P(order ends up in a multi-order route) | extra minutes, near-pickup/near-drop/near-both counts (1.5 km, 20 min), hour, is_food | Logistic regression |

The trained-model metrics are written to `models/metadata.json` and summarised in
[simulation.md](simulation.md#prediction-model-quality).
