.DEFAULT_GOAL := help
SHELL := /bin/bash

PYTHON_BIN ?= python3.11
VENV := .venv
PY := $(VENV)/bin/python
SERVICES := auth account transaction payment fraud notification
COMPOSE := docker compose
# RS256 development keys (see scripts/dev_keys.sh); never committed (secrets/ is git-ignored).
JWT_SIGNING_KEY_ID ?= dev-local-1
JWT_ENV := JWT_SIGNING_KEY_ID=$(JWT_SIGNING_KEY_ID) \
	JWT_PUBLIC_KEYS_DIR=$(abspath secrets/jwt/public) \
	JWT_PRIVATE_KEY_FILE=$(abspath secrets/jwt/private/$(JWT_SIGNING_KEY_ID).pem)

.PHONY: help install test test-services test-e2e lint format run run-service up down logs ps \
        seed-demo seed-account recover reset-dev dev-keys preflight

help: ## Show available targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'

install: ## Create .venv (Python 3.11+) and install all dependencies
	$(PYTHON_BIN) -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements-dev.txt -c constraints.txt

test: test-services test-e2e ## Run every test suite

test-services: ## Unit + integration tests for each service (isolated temp DBs)
	@set -e; for s in $(SERVICES); do \
		echo "== $$s-service"; \
		(cd services/$$s-service && ../../$(PY) -m pytest -p no:cacheprovider); \
	done

test-e2e: ## Cross-service tests (starts all six services on free ports)
	$(PY) -m pytest -p no:cacheprovider tests/e2e

dev-keys: ## Create the local RS256 JWT key pair in secrets/jwt (never overwrites)
	PYTHON=$(abspath $(PY)) DEV_JWT_KID=$(JWT_SIGNING_KEY_ID) scripts/dev_keys.sh

preflight: ## Read-only environment checks (disk, Docker, tools, keys, PostgreSQL)
	$(PY) scripts/preflight.py $(PREFLIGHT_ARGS)

lint: ## Ruff lint + format check
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

format: ## Apply ruff formatting and safe fixes
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .

run: ## Run all six services locally without Docker (Ctrl-C stops)
	PYTHON=$(abspath $(PY)) scripts/run_local.sh

run-service: ## Run one service locally: make run-service SERVICE=auth PORT=8001
	cd services/$(SERVICE)-service && $(JWT_ENV) ../../$(PY) -m uvicorn --factory app.main:create_app --host 127.0.0.1 --port $(PORT)

up: ## Build and start the Docker Compose stack
	$(COMPOSE) up --build -d
	$(COMPOSE) ps

down: ## Stop the Docker Compose stack (keeps data volumes)
	$(COMPOSE) down

logs: ## Follow Docker Compose logs
	$(COMPOSE) logs -f --tail=100

ps: ## Show Docker Compose service status
	$(COMPOSE) ps

seed-demo: ## Run the demo workflow against running services (dev only)
	$(PY) scripts/demo.py

seed-account: ## Dev-only funds via CLI in Docker: make seed-account ACCOUNT_ID=... AMOUNT=5000.00
	$(COMPOSE) exec account-service python -m app.dev_seed --account-id $(ACCOUNT_ID) --amount $(AMOUNT)

recover: ## Trigger one recovery pass in transaction- and payment-service
	@TOKEN=$${INTERNAL_API_TOKEN:-dev-only-insecure-internal-token-do-not-use-in-production}; \
	curl -fsS -X POST -H "X-Internal-Token: $$TOKEN" http://localhost:8003/internal/recovery/run; echo; \
	curl -fsS -X POST -H "X-Internal-Token: $$TOKEN" http://localhost:8004/internal/recovery/run; echo

reset-dev: ## DELETE all local dev data (requires CONFIRM=yes)
	CONFIRM=$(CONFIRM) scripts/reset_dev.sh

k8s-up: ## Deploy MeshBank onto local kind Kubernetes cluster
	powershell -File k8s/deploy.ps1

k8s-down: ## Tear down the MeshBank kind Kubernetes cluster
	kind delete cluster --name meshbank

k8s-status: ## Check Kubernetes pod and service status
	kubectl get pods,svc,endpoints -n meshbank

k8s-test: ## Run the Kubernetes banking smoke test
	$(PY) k8s/k8s-smoke-test.py

frontend: ## Start the MeshBank React + Vite + Tailwind dashboard (http://localhost:5173)
	cd frontend && npm run dev

