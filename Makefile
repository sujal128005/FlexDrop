# Convenience targets (no Docker). Run `make install` first.
PY ?= python3
VENV ?= .venv
BIN = $(VENV)/bin

.PHONY: install test test-fast lint format pipeline robustness demo studio serve migrate clean

install:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -c constraints.txt -e ".[dev]"

test:
	$(BIN)/pytest -q --cov=flexdrop --cov-report=term

test-fast:
	$(BIN)/pytest -q -m "not slow"

lint:
	$(BIN)/ruff check src tests migrations scripts
	$(BIN)/ruff format --check src tests migrations scripts
	$(BIN)/mypy

format:
	$(BIN)/ruff format src tests migrations
	$(BIN)/ruff check --fix src tests migrations

pipeline:
	$(BIN)/flexdrop pipeline --data data/sample --models models --reports reports

robustness:
	$(BIN)/flexdrop robustness --models models --reports reports

demo:
	$(BIN)/flexdrop demo --models models

studio:
	$(BIN)/flexdrop studio --models models --reports reports

serve:
	$(BIN)/flexdrop serve --host 127.0.0.1 --port 8000

migrate:
	$(BIN)/alembic upgrade head

clean:
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov *.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
