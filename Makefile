# Intrv-embed developer commands. Run `make help` for the list.
SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := docker compose --profile core
BACKEND := cd backend &&
FRONTEND := cd frontend &&

.PHONY: help env up down logs ps test test-unit test-integration lint fmt openapi \
        migrate seed create-owner e2e selftest

help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

env: ## Create .env with freshly generated secrets (refuses to overwrite)
	python3 scripts/gen_env.py

up: ## Start the core stack (Postgres, Valkey, Qdrant, SeaweedFS, backend, worker, beat, frontend)
	@test -f .env || { echo ".env missing: run 'make env' first"; exit 1; }
	$(COMPOSE) up -d --build --wait

down: ## Stop the stack (data volumes are kept)
	$(COMPOSE) down

logs: ## Follow logs of all services
	$(COMPOSE) logs -f --tail=100

ps: ## Show service status
	$(COMPOSE) ps

test: test-unit test-integration ## Backend unit + integration tests, frontend typecheck

test-unit: ## Fast tests, no Docker needed
	$(BACKEND) uv run pytest -m "not integration and not live"
	$(FRONTEND) npm run typecheck

test-integration: ## Tests against real containers (needs Docker)
	$(BACKEND) uv run pytest -m integration

lint: ## Ruff, mypy, ESLint, OpenAPI/type drift checks
	$(BACKEND) uv run ruff check . && uv run ruff format --check . && uv run mypy
	$(BACKEND) uv run python -m app.export_openapi | diff -q - openapi.json >/dev/null || \
		{ echo "backend/openapi.json is stale: run 'make openapi'"; exit 1; }
	$(FRONTEND) npm run lint && npm run typecheck

fmt: ## Auto-format backend code
	$(BACKEND) uv run ruff check --fix . && uv run ruff format .

openapi: ## Regenerate backend/openapi.json and frontend API types
	$(BACKEND) uv run python -m app.export_openapi openapi.json
	$(FRONTEND) npm run gen:api

migrate: ## Apply database migrations (also runs automatically on `make up`)
	$(COMPOSE) run --rm migrate

seed: ## Load role packs, provider presets and demo data (available from Phase 2/5)
	@echo "make seed: available from Phase 2 (provider presets) and Phase 5 (role packs)"; exit 1

create-owner: ## Create an org owner: make create-owner ORG=acme EMAIL=you@acme.com NAME="Your Name"
	@test -n "$(ORG)" -a -n "$(EMAIL)" -a -n "$(NAME)" || { echo 'usage: make create-owner ORG=slug EMAIL=you@x.com NAME="Your Name"'; exit 1; }
	$(COMPOSE) exec backend python -m app.cli create-owner --org-slug "$(ORG)" --org-name "$(ORG)" --email "$(EMAIL)" --name "$(NAME)" --password-stdin

e2e: ## Playwright end-to-end tests (available from Phase 6)
	@echo "make e2e: available from Phase 6"; exit 1

selftest: ## Model load and dimension checks (available from Phase 3)
	@echo "make selftest: available from Phase 3"; exit 1
