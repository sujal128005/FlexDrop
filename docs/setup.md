# Setup and execution

No Docker is used or required anywhere in this project.

## Prerequisites

* Python **3.11+** (developed and verified on 3.13)
* Linux or macOS; Windows works under WSL2
* About 500 MB of disk for the virtual environment (OR-Tools and scikit-learn wheels)
* Optional: PostgreSQL 13+ for production-like runs

## Install

```bash
cd flexdrop
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -c constraints.txt -e ".[dev]"   # add ,postgres for the PostgreSQL driver
```

`constraints.txt` pins the exact package versions the project was verified with (Python 3.13),
including scikit-learn 1.9.1, the version the shipped models were trained with. Without it, pip
picks the newest compatible versions, which also works; the models then fall back to analytic
priors until you retrain with `flexdrop pipeline`.

## Verify

```bash
pytest -q                                   # full suite (~1.5 min)
pytest -q -m "not slow"                     # skip the end-to-end pipeline test
pytest --cov=flexdrop --cov-report=term     # coverage (~96%)
ruff check src tests && ruff format --check src tests
mypy                                        # static type check (clean)
```

## Run the experiments

```bash
# 1. everything: data -> training -> coordination calibration -> 5 policies x 2 scenarios
flexdrop pipeline --data data/sample --models models --reports reports

# 2. or step by step
flexdrop generate-data --scenario campus --out data/sample
flexdrop train --data data/sample --models models
flexdrop simulate --scenario campus --policies all --models models --out reports
flexdrop simulate --scenario city --policies independent,batching,flexdrop --duration 120

# 3. robustness across seeds (mean ± std)
flexdrop robustness --seeds 7,8,9,10,11
```

Outputs:
* `reports/SUMMARY.md`: comparison tables.
* `reports/<scenario>_metrics.json`: all KPIs.
* `reports/<scenario>_<policy>_orders.csv`: per-order outcomes.
* `reports/ROBUSTNESS.md`.
* `models/metadata.json`: model metrics.

## Run the service

```bash
flexdrop create-api-key --name local-admin --roles admin     # prints a key and a JSON entry
cp .env.example .env                                          # then paste the JSON into FLEXDROP_API_KEYS
flexdrop serve --host 127.0.0.1 --port 8000                   # dev: schema auto-created in SQLite
```

For the browser prototype with a ready-made campus, run `flexdrop studio` and open
`http://127.0.0.1:8000/app/` ([frontend.md](frontend.md)). For the API explorer, open
`http://127.0.0.1:8000/docs`. To run the scripted end-to-end demo in the terminal:

```bash
flexdrop demo --models models          # uses a fresh temporary database every run
```

To smoke-test a server that is already running (creates a store, a partner and two orders,
optimises, executes the plan and reads metrics):

```bash
FLEXDROP_URL=http://127.0.0.1:8000 FLEXDROP_KEY=<admin key> bash scripts/smoke_test.sh
```

## Configuration reference (environment, prefix `FLEXDROP_`)

| Variable | Default | Meaning |
|---|---|---|
| `ENV` | `development` | `development`, `test`, `staging`, `production` (production enforces secrets and keys, and disables docs) |
| `DATABASE_URL` | `sqlite:///./flexdrop.db` | SQLAlchemy URL |
| `API_KEYS` | `{}` | JSON `{sha256: {name, roles}}` |
| `PSEUDONYMIZATION_SECRET` | dev value | HMAC key for customer IDs (≥ 32 chars in production) |
| `MODEL_DIR` | `models` | Trained artefacts (analytic priors are used if missing) |
| `DEPLOYMENT_LEVEL` | `1` | 1 same-category, 2 cross-category, 3 multi-order + insertion |
| `SOLVER` | `cpsat` | `cpsat` or `greedy` |
| `CPSAT_TIME_LIMIT_S` | `2.0` | Per-epoch solver time limit |
| `RATE_LIMIT_PER_MINUTE` | `600` | Per API key |
| `MAX_REQUEST_BYTES` | `1000000` | Request body limit |
| `PROMETHEUS_PUBLIC` | `false` | Expose `/metrics/prometheus` without a key |
| `DATA_RETENTION_DAYS` | `90` | `flexdrop purge` default |
| `REPORTS_DIR` | `reports` | Folder read by `/v1/reports/simulation` (Results screen) |
| `KILL_SWITCH_DEFAULT` | `false` | Kill-switch value before any flag is stored |
| `AUTO_CREATE_SCHEMA` | `true` | Create tables at start-up (ignored in production) |
| `EXPOSE_DOCS` | `true` | OpenAPI UI (always off in production) |
| `LOG_LEVEL`, `LOG_JSON` | `INFO`, `true` | Structured logging |

Engine parameters (costs, limits, penalties) are code-level defaults in `config.py::EngineConfig`
and are documented in [math_model.md](math_model.md).

## Windows notes

There are three ways to run the project on Windows. Always run commands inside the extracted
`flexdrop` folder, not `C:\Windows\System32`.

1. **Native PowerShell** (needs Python 3.11+ from https://www.python.org/downloads/ with
   "Add python.exe to PATH" ticked):

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1
   .venv\Scripts\python.exe -m flexdrop studio      # then open http://127.0.0.1:8000/app/
   ```

2. **WSL (Ubuntu)**. `bash scripts/run_all.sh` needs Python inside Linux:

   ```bash
   sudo apt update && sudo apt install -y python3 python3-venv python3-pip
   bash scripts/run_all.sh
   .venv/bin/python -m flexdrop studio
   ```

3. **Git Bash**: `bash scripts/run_all.sh` finds `python3`, `python` or the `py` launcher and uses
   the Windows virtual-environment layout automatically.

If Windows **Smart App Control / Application Control** blocks `pip.exe` or reports
`DLL load failed ... An Application Control policy has blocked this file`, Windows itself is
refusing to run unsigned native libraries (pydantic, numpy, scikit-learn, OR-Tools). The scripts
already use `python -m pip`; if the DLL block remains, use **WSL** or **Google Colab** (below).
Where console scripts are blocked, use the module form: `python -m pytest`, `python -m flexdrop <command>`.

## Google Colab

In a new notebook (https://colab.research.google.com), run each block as a cell:

```python
from google.colab import files
files.upload()   # choose FlexDrop_Nexara9.zip
```

```python
!rm -rf flexdrop && unzip -q FlexDrop_Nexara9.zip
%cd /content/flexdrop
!pip install -q -c constraints.txt -e ".[dev]"
```

```python
!python -m pytest -q
!python -m flexdrop --log-level WARNING pipeline   # retrains models for Colab's scikit-learn
!python -m flexdrop --log-level WARNING demo
!cat reports/SUMMARY.md
```

To open the browser prototype (Colab shows a link that opens it in a new tab):

```python
import sys
sys.path.insert(0, "/content/flexdrop/src")   # import the package without restarting the runtime
from flexdrop.colab import launch
launch()
```

Red `pip's dependency resolver` messages about Colab's preinstalled packages are harmless. To
restart the website: `from flexdrop.colab import stop; stop()`, then `launch()`.

Colab ships its own scikit-learn version. Trained model files are only loaded when they were
trained with the installed version; otherwise the service uses analytic priors and logs a
warning. Running `pipeline` (or `train`) retrains the models for the current environment.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `401 unauthenticated` | Send `X-API-Key`. `FLEXDROP_API_KEYS` must contain the SHA-256 of that key (`flexdrop create-api-key`) |
| Offers never appear | Normal for the first orders in an empty area: offers need coordination potential (`offers_unavailable_reason`) |
| `/v1/optimize` returns no plans | Check `deferred_order_ids` (food not ready yet: just-in-time dispatch) and idle partners |
| `trained with scikit-learn X but Y is installed` warning | Model files came from another scikit-learn version, so analytic priors are in use. Run `flexdrop pipeline` or `flexdrop train` to retrain |
| Slow optimisation | Lower `CPSAT_TIME_LIMIT_S`, shard with `zone`, or use `SOLVER=greedy` |
