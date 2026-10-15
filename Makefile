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
        seed-demo seed-account recover reset-dev dev-keys preflight frontend \
        k8s-up k8s-down k8s-status k8s-test k8s-mtls-test k8s-canary-split k8s-canary-demo mesh-configs \
        k8s-multi-up k8s-multi-down k8s-multi-status k8s-multi-test k8s-multi-mtls-test k8s-multi-canary-demo

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

lint: ## Ruff lint + format check, generated mesh configs up to date
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .
	cd scripts && ../$(PY) generate_mesh_sidecars.py --check

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

k8s-up: ## Build images and deploy MeshBank (with mTLS mesh + canary) to a local kind cluster
	PYTHON=$(abspath $(PY)) bash k8s/deploy.sh

k8s-down: ## Delete the MeshBank kind cluster (all in-cluster data is lost)
	bash k8s/delete.sh single

k8s-status: ## Check Kubernetes pod and service status
	kubectl get pods,svc,endpoints -n meshbank

k8s-test: ## Run the Kubernetes banking smoke test (every service hop over mTLS)
	$(PY) k8s/k8s-smoke-test.py

k8s-mtls-test: ## Mesh security tests: plaintext bypass, untrusted certs, caller authorization
	$(PY) k8s/test-mesh-mtls.py

k8s-canary-split: ## Set the payment canary split: make k8s-canary-split STABLE=90 CANARY=10
	$(PY) k8s/canary-traffic.py --set-split $(STABLE) $(CANARY)

k8s-canary-demo: ## Canary rollout demo: 90/10 -> 50/50 -> 0/100 -> rollback
	$(PY) k8s/canary-traffic.py --demo-rollout

k8s-multi-up: ## Two kind clusters simulating AWS + GCP, joined by mTLS east-west gateways
	PYTHON=$(abspath $(PY)) bash k8s/deploy-multicluster.sh

k8s-multi-down: ## Delete both multi-cluster kind clusters (all in-cluster data is lost)
	bash k8s/delete.sh multi

k8s-multi-status: ## Pods in both clusters, with the cloud each node simulates
	@for c in aws gcp; do echo "== kind-meshbank-$$c"; \
		kubectl --context kind-meshbank-$$c get nodes -L meshbank.io/cloud --no-headers; \
		kubectl --context kind-meshbank-$$c get pods -n meshbank -o wide; done

k8s-multi-test: ## Banking smoke test across both clusters (cross-cluster hops over mTLS)
	MESHBANK_TOPOLOGY=multi $(PY) k8s/k8s-smoke-test.py

k8s-multi-mtls-test: ## Mesh security tests for both clusters, including the gateways
	MESHBANK_TOPOLOGY=multi $(PY) k8s/test-mesh-mtls.py

k8s-multi-canary-demo: ## Canary rollout demo on the gcp cluster
	MESHBANK_TOPOLOGY=multi $(PY) k8s/canary-traffic.py --demo-rollout

mesh-configs: ## Regenerate sidecar and gateway configs from scripts/mesh_topology.py
	cd scripts && ../$(PY) generate_mesh_sidecars.py

frontend: ## Dashboard at http://localhost:5173 (two clusters: make frontend MESHBANK_TOPOLOGY=multi)
	cd frontend && npm run dev

