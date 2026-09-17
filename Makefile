.DEFAULT_GOAL := help

PYTHON  ?= python3.12
VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
PYTEST  := $(VENV)/bin/pytest
RUFF    := $(VENV)/bin/ruff
COMPOSE := docker compose -f infra/docker-compose.yml

ALEMBIC := cd packages/autora && ../../$(VENV)/bin/alembic

.PHONY: help setup dev down up logs migrate migration db-check gen-schema gen-schema-check phase1-acceptance test test-py test-web lint lint-py lint-web clean

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip

setup: $(VENV)/bin/activate ## Create venv, install Python package (editable) and JS deps
	$(PIP) install -e "packages/autora[dev]"
	pnpm install
	@test -f .env || cp .env.example .env

dev: ## Start Postgres (pgvector) only and wait until healthy
	$(COMPOSE) up -d db
	@echo "waiting for db..."
	@for i in $$(seq 1 30); do \
	  if $(COMPOSE) exec -T db pg_isready -U autora -d autora >/dev/null 2>&1; then echo "db ready on localhost:$${AUTORA_DB_PORT:-5434}"; exit 0; fi; \
	  sleep 1; \
	done; echo "db did not become ready" && exit 1

up: ## Start full stack (db + api + worker + web) via Docker
	$(COMPOSE) --profile full up -d --build

down: ## Stop all containers
	$(COMPOSE) --profile full down

logs: ## Tail container logs
	$(COMPOSE) --profile full logs -f --tail=100

migrate: ## Apply database migrations (alembic upgrade head)
	$(ALEMBIC) upgrade head

db-check: ## Fail if models and migrations have drifted (alembic check)
	$(ALEMBIC) check

migration: ## Create a new migration: make migration m="add foo"
	$(ALEMBIC) revision --autogenerate -m "$(m)"

gen-schema: ## Regenerate packages/event-schema from the pydantic event registry
	$(PY) scripts/gen_event_schema.py

gen-schema-check: ## Fail if packages/event-schema is stale
	$(PY) scripts/gen_event_schema.py --check

phase1-acceptance: ## Phase 1 AC: agent walks 8 states in Postgres; TS parses the real events
	AUTORA_EVENT_FIXTURE_OUT=$(CURDIR)/packages/event-schema/test/fixtures/phase1-events.json \
		$(PYTEST) packages/autora/tests/acceptance/test_phase1.py -q
	pnpm -F @autora/event-schema test

test: test-py test-web ## Run all tests

test-py: ## Run Python tests
	$(PYTEST) packages/autora -q

test-web: ## Run JS tests
	pnpm test

lint: lint-py lint-web ## Run all linters

lint-py:
	$(RUFF) check packages/autora apps/api apps/worker scripts
	$(RUFF) format --check packages/autora apps/api apps/worker scripts
	$(VENV)/bin/lint-imports

lint-web:
	pnpm lint

clean: ## Remove venv, node_modules, caches
	rm -rf $(VENV) node_modules apps/web/node_modules apps/web/.next .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
