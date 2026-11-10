#!/usr/bin/env bash
# One-shot: install, lint, test, run the experiment pipeline and the API demo. No Docker.
# Works on Linux, macOS, WSL and Git Bash on Windows. Set PYTHON=/path/to/python to choose one.
set -euo pipefail
cd "$(dirname "$0")/.."

# Find a Python 3.11+ interpreter (python3, python or the Windows "py" launcher).
find_python() {
  local cand
  for cand in "${PYTHON:-}" python3 python "py -3"; do
    [ -z "$cand" ] && continue
    # shellcheck disable=SC2086
    if $cand -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      echo "$cand"
      return 0
    fi
  done
  return 1
}
if ! PY="$(find_python)"; then
  echo "error: Python 3.11 or newer was not found." >&2
  echo "  WSL / Ubuntu : sudo apt update && sudo apt install -y python3 python3-venv python3-pip" >&2
  echo "  Windows      : install from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')," >&2
  echo "                 then run: powershell -ExecutionPolicy Bypass -File scripts\\run_all.ps1" >&2
  exit 1
fi
echo "using $($PY -c 'import sys; print(sys.executable, sys.version.split()[0])')"

if [ ! -d .venv ]; then
  # shellcheck disable=SC2086
  $PY -m venv .venv
fi
# Linux/macOS/WSL put the interpreter in .venv/bin, Windows in .venv/Scripts
if [ -x .venv/bin/python ]; then VPY=.venv/bin/python; else VPY=.venv/Scripts/python.exe; fi

"$VPY" -m pip install --quiet --upgrade pip
"$VPY" -m pip install --quiet -c constraints.txt -e ".[dev]"

echo "== lint =="
"$VPY" -m ruff check src tests migrations scripts
echo "== type-check =="
"$VPY" -m mypy
echo "== tests =="
"$VPY" -m pytest -q
echo "== experiment pipeline =="
"$VPY" -m flexdrop --log-level WARNING pipeline --data data/sample --models models --reports reports
echo "== API demo =="
"$VPY" -m flexdrop --log-level WARNING demo --models models
echo "done: see reports/SUMMARY.md"
echo "browser prototype: run '$VPY -m flexdrop studio' and open http://127.0.0.1:8000/app/"
