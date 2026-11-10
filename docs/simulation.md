# Simulation and results

## 1. Purpose

The proposal (Phase 2) asks for FlexDrop to be compared against independent dispatch and
conventional batching on realistic synthetic data. The simulator in `src/flexdrop/simulation/`
runs the **same engine code as the API** against a ground-truth world that the engines never see
directly.

## 2. Synthetic world

| | Campus | City |
|---|---|---|
| Stores | 35 restaurants + 2 Instamart, 0.6–3 km around the campus | 60 restaurants + 4 Instamart in a 3.5 km area |
| Drops | 10 hostel gates (88%) + 40 off-campus points | 400 dispersed residential points |
| Customers | 2 500, 90% students | 4 000, 15% students |
| Partners | 70 (10% with small, single-compartment bags) | 76 |
| Window | 19:00–23:00 IST; 1.3 → 2.6 → 0.9 orders/min | 19:00–23:00 IST; 1.3 → 2.2 → 0.8 orders/min |
| Mix | 80% food / 20% grocery | 75% / 25% |

Ground truth (`scenario.py::GroundTruth`):
* noisy preparation times (store base time, items, load, evening peak);
* congestion-dependent noisy travel times;
* customer utility for offers (reward, wait × urgency, price sensitivity, student);
* 2% cancellations;
* restaurant delay signals;
* partner drop-outs.

Idle partners reposition to demand hubs. The scenario is identical across policies: same orders,
same preparation times, same cancellations.

## 3. Events

`ORDER` (predict → promise → quote → customer choice), `EPOCH` (DCO every minute), `ARRIVE`
(stop execution against the true ready time), `PREP_DELAY` / `CANCEL` / `DROPOUT` (CRE).

## 4. Policies

| Policy | Description |
|---|---|
| `independent` | One order per route, FIFO, nearest feasible partner, just-in-time dispatch |
| `batching` | Conventional batching: same-store pairs (≤ 300 m), only within standard promises, greedy; no flexibility |
| `flexdrop_noflex` | FlexDrop optimiser (groups ≤ 3, CP-SAT) without flexible offers: the ablation |
| `flexdrop` | Full FlexDrop, Level 1 |
| `flexdrop_l3` | Level 3: cross-category, groups ≤ 4, insertion into live routes |

## 5. KPI definitions

| KPI | Definition |
|---|---|
| Fulfilment cost / order | (Σ actual route cost incl. earnings top-ups + Σ rewards paid) / delivered orders |
| On-time rate | Delivered at or before the **approved** commitment (standard promise + approved flexibility) |
| Delivered by standard promise | Delivered at or before the standard promise (flexible orders legitimately miss it) |
| Coordinated share | Delivered orders that shared a route with at least one other order |
| Food-quality violations | Handover later than the age limit after the **true** ready time |
| Flexibility used / approved | Σ s_i / Σ w_i over flexible orders |
| Partner earnings / engaged hour | Σ partner pay / Σ engaged hours |
| Latency | Wall-clock time of each optimisation epoch (P50, P95, max) |

## 6. Results (seed 42, 240 min)

Reproduce with `flexdrop pipeline`. The full tables are in `reports/SUMMARY.md`.

**Campus** (470 orders):

| Policy | Cost / order | Δ cost | km / order | On-time | Coordinated | Avg click-to-door |
|---|---:|---:|---:|---:|---:|---:|
| independent | ₹78.56 | — | 4.56 | 90.2% | 0% | 29.8 min |
| batching | ₹73.78 | −6.1% | 4.16 | 92.1% | 19% | 29.7 min |
| flexdrop_noflex | ₹70.63 | −10.1% | 3.95 | 88.5% | 34% | 30.2 min |
| **flexdrop** | **₹66.21** | **−15.7%** | **3.30** | **92.8%** | **63%** | 32.3 min |
| flexdrop_l3 | ₹63.05 | −19.7% | 3.06 | 89.3% | 72% | 32.9 min |

**City** (404 orders):

| Policy | Cost / order | Δ cost | km / order | On-time | Coordinated | Avg click-to-door |
|---|---:|---:|---:|---:|---:|---:|
| independent | ₹83.14 | — | 5.00 | 95.8% | 0% | 30.1 min |
| batching | ₹79.76 | −4.1% | 4.67 | 95.3% | 4% | 29.9 min |
| flexdrop_noflex | ₹76.91 | −7.5% | 4.44 | 95.3% | 13% | 30.0 min |
| **flexdrop** | **₹72.84** | **−12.4%** | **3.92** | **95.0%** | **28%** | 32.1 min |
| flexdrop_l3 | ₹72.24 | −13.1% | 3.91 | 96.0% | 36% | 32.4 min |

FlexDrop costs **include** rewards: ₹4.50 per order on campus and ₹2.63 in the city.

### Seed robustness (5 independent scenarios per area, `reports/ROBUSTNESS.md`)

| Area | Policy | Cost vs independent | On-time vs independent |
|---|---|---:|---:|
| Campus | batching | −7.9% ± 1.7 | −0.1 ± 2.3 pp |
| Campus | **flexdrop** | **−15.5% ± 1.5** | **+0.1 ± 2.1 pp** |
| City | batching | −2.0% ± 1.0 | +0.6 ± 1.3 pp |
| City | **flexdrop** | **−10.8% ± 1.3** | **+0.3 ± 2.9 pp** |

## 7. Interpretation

1. **Customer flexibility is the main driver.** Optimisation alone (`flexdrop_noflex`) gives
   about 7–10%. Adding flexibility roughly doubles the coordinated share and adds another 5–6
   points of cost reduction **after paying the rewards**.
2. **The campus is the strongest use case.** Hostel-gate drops, dense restaurant clusters and
   price-sensitive students give 63% coordination and −15.7% cost. Dispersed city demand still
   gives −11 to −12%. This supports the student-campus framing of the case.
3. **Flexibility is used sparingly.** Only 14–15% of approved flexible minutes are consumed
   (minimum-use allocation). Average click-to-door rises by about 2.5 minutes, and only for
   customers who agreed and were paid for it.
4. **Safety holds.** Level 1 had zero food-quality violations in both areas (conventional
   batching had 4 and 1). Partner waiting at stores falls slightly (campus 2.66 → 2.56 min, city
   3.17 → 2.91 min).
5. **Partner earnings.** Earnings per engaged hour stay well above the ₹140 floor (₹168–170 vs
   ₹170–172 for independent dispatch), and partners complete 19–25% more orders per engaged hour. Total partner pay
   falls because routes are fewer and shorter. Section 9 explains why this needs an explicit
   policy decision.
6. **Level 3** brings further savings on campus (−19.7%) at a slightly lower on-time rate. This
   is consistent with the proposal's staged rollout: L1 first, L3 only after the pilot gates pass.

### Pilot acceptance criteria (proposal §13), FlexDrop L1

| Criterion | Campus | City |
|---|---|---|
| ≥ 5% fulfilment-cost reduction | ✅ −15.7% (−15.5 ± 1.5 over seeds) | ✅ −12.4% (−10.8 ± 1.3) |
| ≤ 1 pp on-time decline vs control | ✅ +2.6 pp (+0.1 ± 2.1) | ✅ −0.8 pp (+0.3 ± 2.9) |
| Zero food-safety violations | ✅ 0 | ✅ 0 |
| Optimisation latency < 5 s | ✅ P95 51 ms | ✅ P95 54 ms |
| No reduction in protected partner earnings | ✅ floor respected, ₹168/engaged h | ✅ ₹170/engaged h |

## 8. Sensitivity

**Hold safety margin `h`** (FlexDrop L1, seed 42, models from an earlier training run):

| h (min) | City cost | City on-time | Campus cost | Campus on-time |
|---:|---:|---:|---:|---:|
| 3 | ₹72.81 | 94.3% | ₹66.61 | 92.1% |
| 4 | ₹72.28 | 92.8% | ₹66.20 | 91.3% |
| **5** (default) | ₹73.64 | 95.3% | ₹66.07 | 92.6% |
| 6 | ₹74.45 | 96.0% | ₹66.48 | 91.3% |

A larger `h` dispatches held orders earlier: on-time improves in dispersed areas, at a small cost.
`h` is a per-zone tuning lever. The risk penalty ρ (25 → 60) changed results very little.

## 9. Limitations

* Synthetic world: magnitudes depend on the assumed cost structure, density and customer
  utilities ([assumptions.md](assumptions.md)).
* Single-seed differences of about ±2 pp in on-time rate are within noise; use the robustness run
  for claims.
* A road factor replaces a road network.
* Partner supply is fixed per scenario. A real fleet would adjust, and the reduced total partner
  pay must be addressed through fleet sizing or by sharing part of the savings with partners.

## 10. Prediction model quality

From `models/metadata.json` (held-out 20%):

| Model | Metric | Learned | Analytic prior |
|---|---|---:|---:|
| Prep time | MAE (min) | 2.38 | 2.52 |
| Prep time | P90 coverage | 0.89 | — |
| Travel time | MAE (min) | 2.61 | 2.91 |
| Acceptance | AUC | 0.895 | 0.895 |
| Acceptance | coefficient signs | reward +0.218, wait −0.157 | — |
| Coordination | AUC | 0.798 | 0.775 |
