# Seed robustness

Scenario seeds: [7, 8, 9, 10, 11]; duration 240 min.

## `campus`

| Policy | Cost / order (INR) | Cost vs independent | On-time rate | On-time vs independent (pp) | Coordinated share |
|---|---:|---:|---:|---:|---:|
| `independent` | 75.76 ± 2.09 | +0.0% ± 0.0 | 0.944 ± 0.027 | +0.0 ± 0.0 | 0.000 |
| `batching` | 69.80 ± 2.69 | -7.9% ± 1.7 | 0.943 ± 0.008 | -0.1 ± 2.3 | 0.201 |
| `flexdrop` | 63.97 ± 1.65 | -15.5% ± 1.5 | 0.945 ± 0.010 | +0.1 ± 2.1 | 0.627 |

## `city`

| Policy | Cost / order (INR) | Cost vs independent | On-time rate | On-time vs independent (pp) | Coordinated share |
|---|---:|---:|---:|---:|---:|
| `independent` | 80.27 ± 2.38 | +0.0% ± 0.0 | 0.957 ± 0.025 | +0.0 ± 0.0 | 0.000 |
| `batching` | 78.65 ± 2.20 | -2.0% ± 1.0 | 0.963 ± 0.016 | +0.6 ± 1.3 | 0.026 |
| `flexdrop` | 71.55 ± 1.69 | -10.8% ± 1.3 | 0.960 ± 0.012 | +0.3 ± 2.9 | 0.243 |

