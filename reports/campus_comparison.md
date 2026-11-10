### Scenario: `campus`

| Metric | `independent` | `batching` | `flexdrop_noflex` | `flexdrop` | `flexdrop_l3` |
|---|---:|---:|---:|---:|---:|
| Orders delivered | 469 | 469 | 469 | 470 | 469 |
| Fulfilment cost / order incl. rewards (INR) | 78.56 | 73.78 | 70.63 | 66.21 | 63.05 |
| Delivery cost / order excl. rewards (INR) | 78.56 | 73.78 | 70.63 | 61.71 | 58.63 |
| Rewards / order (INR) | 0 | 0 | 0 | 4.5 | 4.418 |
| Distance / order (km) | 4.564 | 4.155 | 3.95 | 3.303 | 3.063 |
| Orders per route | 0.9832 | 1.076 | 1.173 | 1.446 | 1.574 |
| Coordinated share of orders | 0 | 0.1876 | 0.3369 | 0.6277 | 0.7186 |
| On-time rate (within approved window) | 0.9019 | 0.9211 | 0.8849 | 0.9277 | 0.8934 |
| Delivered by standard promise | 0.9019 | 0.9211 | 0.8849 | 0.766 | 0.7143 |
| Avg click-to-door (min) | 29.77 | 29.69 | 30.16 | 32.26 | 32.86 |
| P90 click-to-door (min) | 40.49 | 41.74 | 41.86 | 44.96 | 45.22 |
| Food-quality violations | 0 | 4 | 2 | 0 | 1 |
| Partner earnings / engaged hour (INR) | 172.5 | 169.9 | 169 | 168.2 | 166.9 |
| Orders / partner engaged hour | 2.284 | 2.398 | 2.493 | 2.844 | 2.972 |
| Avg partner wait at store (min) | 2.656 | 3.138 | 2.986 | 2.557 | 2.595 |
| Flexible-offer acceptance rate | 0 | 0 | 0 | 0.6256 | 0.6158 |
| Share of orders on flexible delivery | 0 | 0 | 0 | 0.5292 | 0.5208 |
| Flexibility used / approved | 0 | 0 | 0 | 0.1547 | 0.1898 |
| Fallback (late-risk) routes | 42 | 16 | 12 | 5 | 4 |
| Re-optimisation events | 55 | 42 | 54 | 63 | 54 |
| Insertions into live routes | 0 | 0 | 0 | 0 | 43 |
| Optimisation latency P95 (ms) | 57.2 | 35.62 | 49.57 | 50.86 | 61.21 |

Relative change vs `independent`:

| Policy | Cost / order | Distance / order | On-time rate (pp) | Avg click-to-door |
|---|---:|---:|---:|---:|
| `batching` | -6.1% | -8.9% | +1.9 | -0.3% |
| `flexdrop_noflex` | -10.1% | -13.4% | -1.7 | +1.3% |
| `flexdrop` | -15.7% | -27.6% | +2.6 | +8.4% |
| `flexdrop_l3` | -19.7% | -32.9% | -0.9 | +10.4% |
