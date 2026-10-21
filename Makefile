# Intrv-embed developer commands. Run `make help` for the list.
SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := docker compose --profile core
BACKEND := cd backend &&
FRONTEND := cd frontend &&

.PHONY: help env up down logs ps test test-unit test-integration test-isolation lint fmt openapi \
        migrate create-owner e2e selftest up-prod down-prod logs-prod backup-now loadtest

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
	cd bridge && uv run pytest -q
	cd voice && uv run pytest -q
	$(FRONTEND) npm run typecheck

test-integration: ## Tests against real containers (needs Docker)
	$(BACKEND) uv run pytest -m integration

test-isolation: ## Tenant and space isolation suite only (needs Docker)
	$(BACKEND) uv run pytest -m isolation

lint: ## Ruff, mypy, ESLint, OpenAPI/type drift checks
	$(BACKEND) uv run ruff check . && uv run ruff format --check . && uv run mypy
	$(BACKEND) uv run python -m app.export_openapi | diff -q - openapi.json >/dev/null || \
		{ echo "backend/openapi.json is stale: run 'make openapi'"; exit 1; }
	$(FRONTEND) npm run lint && npm run format:check && npm run typecheck

fmt: ## Auto-format backend code
	$(BACKEND) uv run ruff check --fix . && uv run ruff format .

openapi: ## Regenerate backend/openapi.json and frontend API types
	$(BACKEND) uv run python -m app.export_openapi openapi.json
	$(FRONTEND) npm run gen:api

migrate: ## Apply database migrations (also runs automatically on `make up`)
	$(COMPOSE) run --rm migrate

create-owner: ## Create an org owner: make create-owner ORG=acme EMAIL=you@acme.com NAME="Your Name" [STACK=prod]
	@test -n "$(ORG)" -a -n "$(EMAIL)" -a -n "$(NAME)" || { echo 'usage: make create-owner ORG=slug EMAIL=you@x.com NAME="Your Name"'; exit 1; }
	$(if $(filter prod,$(STACK)),$(PROD),$(COMPOSE)) exec backend python -m app.cli create-owner --org-slug "$(ORG)" --org-name "$(ORG)" --email "$(EMAIL)" --name "$(NAME)" --password-stdin

e2e: ## Playwright browser tests against the running stack (`make up` first; see e2e/playwright.config.ts)
	cd e2e && npm ci --no-audit --no-fund && npx playwright test

voice-bench: ## Voice pipeline latency per stage (p50/p95); start the TTS container first for ARGS="--tts http"
	cd voice && uv run python -m intrv_voice.bench --rounds 5 $(ARGS)

selftest: ## Model load, vector store and parser checks inside the running backend
	$(if $(filter prod,$(STACK)),$(PROD),$(COMPOSE)) exec backend python -m app.cli selftest

# --- production (one VPS; docs/DEPLOYMENT.md) -----------------------------------------------------------------
PROD := docker compose -f deploy/compose.prod.yml --env-file .env $(foreach p,$(PROFILES),--profile $(p))

up-prod: ## Production stack with Caddy HTTPS and nightly backups (PROFILES="frontend voice coding")
	@test -f .env || { echo ".env missing: run 'make env' and fill the production values"; exit 1; }
	@grep -q '^APP_ENV=prod' .env || { echo "set APP_ENV=prod in .env (docs/DEPLOYMENT.md §6)"; exit 1; }
	$(PROD) up -d --build --wait

down-prod: ## Stop the production stack (volumes are kept)
	$(PROD) down

logs-prod: ## Follow production logs
	$(PROD) logs -f --tail=100

backup-now: ## Run one backup immediately (same as the nightly job)
	$(PROD) run --rm -e BACKUP_NOW=1 backup

loadtest: ## N concurrent text interviews against INTRV_API (default local); needs INTRV_EMAIL/INTRV_PASSWORD (docs/LOADTEST.md)
	cd loadtest && uv run --no-project --with httpx==0.28.1 --with websockets==15.0.1 python interviews.py -n $(or $(N),50)
