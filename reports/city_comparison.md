### Scenario: `city`

| Metric | `independent` | `batching` | `flexdrop_noflex` | `flexdrop` | `flexdrop_l3` |
|---|---:|---:|---:|---:|---:|
| Orders delivered | 404 | 404 | 405 | 402 | 403 |
| Fulfilment cost / order incl. rewards (INR) | 83.14 | 79.76 | 76.91 | 72.84 | 72.24 |
| Delivery cost / order excl. rewards (INR) | 83.14 | 79.76 | 76.91 | 70.21 | 69.61 |
| Rewards / order (INR) | 0 | 0 | 0 | 2.629 | 2.633 |
| Distance / order (km) | 5.003 | 4.669 | 4.435 | 3.924 | 3.913 |
| Orders per route | 0.9854 | 1.01 | 1.052 | 1.152 | 1.207 |
| Coordinated share of orders | 0 | 0.0446 | 0.1284 | 0.2786 | 0.3573 |
| On-time rate (within approved window) | 0.9579 | 0.953 | 0.9531 | 0.9502 | 0.9603 |
| Delivered by standard promise | 0.9579 | 0.953 | 0.9531 | 0.8383 | 0.8362 |
| Avg click-to-door (min) | 30.12 | 29.94 | 30.01 | 32.08 | 32.39 |
| P90 click-to-door (min) | 44.61 | 45.31 | 45.49 | 47.18 | 46.88 |
| Food-quality violations | 0 | 1 | 0 | 0 | 2 |
| Partner earnings / engaged hour (INR) | 170.1 | 168.7 | 168.7 | 169.6 | 169.3 |
| Orders / partner engaged hour | 2.124 | 2.197 | 2.281 | 2.516 | 2.533 |
| Avg partner wait at store (min) | 3.168 | 3.269 | 3.355 | 2.91 | 2.809 |
| Flexible-offer acceptance rate | 0 | 0 | 0 | 0.434 | 0.434 |
| Share of orders on flexible delivery | 0 | 0 | 0 | 0.3317 | 0.3317 |
| Flexibility used / approved | 0 | 0 | 0 | 0.139 | 0.143 |
| Fallback (late-risk) routes | 14 | 10 | 9 | 12 | 9 |
| Re-optimisation events | 27 | 23 | 23 | 30 | 34 |
| Insertions into live routes | 0 | 0 | 0 | 0 | 15 |
| Optimisation latency P95 (ms) | 45.92 | 47.89 | 49.38 | 54.38 | 105.7 |

Relative change vs `independent`:

| Policy | Cost / order | Distance / order | On-time rate (pp) | Avg click-to-door |
|---|---:|---:|---:|---:|
| `batching` | -4.1% | -6.7% | -0.5 | -0.6% |
| `flexdrop_noflex` | -7.5% | -11.4% | -0.5 | -0.4% |
| `flexdrop` | -12.4% | -21.6% | -0.8 | +6.5% |
| `flexdrop_l3` | -13.1% | -21.8% | +0.2 | +7.5% |
