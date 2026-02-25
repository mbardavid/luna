PYTHON ?= python3
PIP    ?= pip3

.PHONY: install run-paper run-api test up down lint

install:
	$(PIP) install -e ".[dev]"

run-paper:
	APP_ENV=paper $(PYTHON) -m core.main

run-api:
	APP_ENV=prod $(PYTHON) -m core.main

test:
	$(PYTHON) -m pytest -v tests/

up:
	docker compose up -d

down:
	docker compose down

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m mypy --ignore-missing-imports config/ core/
