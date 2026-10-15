# MeshBank Kubernetes Deployment

This directory contains the production-grade Kubernetes manifests, configuration, and automation scripts for deploying **MeshBank** to a local Kubernetes cluster (Kind or Minikube).

---

## Architecture Overview

```
                      +---------------------------------------+
                      |       Kubernetes Node (Kind)          |
                      |                                       |
  [:30001] ---------> |  [auth-nodeport] -> auth-service      |
  [:30002] ---------> |  [account-nodeport] -> account-service|
  [:30003] ---------> |  [txn-nodeport] -> transaction-service|
  [:30004] ---------> |  [pay-nodeport] -> payment-service    |
  [:30005] ---------> |  [fraud-nodeport] -> fraud-service    |
  [:30006] ---------> |  [notif-nodeport] -> notif-service    |
                      |                                       |
                      |  [postgres:5432]                      |
                      |    - PersistentVolumeClaim (2Gi)      |
                      |    - 6 isolated DBs + roles           |
                      |    - Automated Alembic migrations     |
                      +---------------------------------------+
```

### Components & Manifests

| Manifest | Kind | Purpose |
|---|---|---|
| [`namespace.yaml`](file:///c:/Projects/MeshBank/k8s/namespace.yaml) | `Namespace` | Creates isolated `meshbank` namespace |
| [`configmap.yaml`](file:///c:/Projects/MeshBank/k8s/configmap.yaml) | `ConfigMap` | Shared service URLs, environment, log level, timeouts |
| [`secrets.yaml`](file:///c:/Projects/MeshBank/k8s/secrets.yaml) | `Secret` | PostgreSQL password, internal API token |
| [`postgres.yaml`](file:///c:/Projects/MeshBank/k8s/postgres.yaml) | `Deployment`, `PVC`, `ConfigMap`, `Service` | PostgreSQL 15 instance with per-service databases and credentials |
| [`auth-service.yaml`](file:///c:/Projects/MeshBank/k8s/auth-service.yaml) | `Deployment`, `Service` | Authentication service with RS256 private key and public key mounts |
| [`account-service.yaml`](file:///c:/Projects/MeshBank/k8s/account-service.yaml) | `Deployment`, `Service` | Account ledger with dev balance seeding enabled |
| [`transaction-service.yaml`](file:///c:/Projects/MeshBank/k8s/transaction-service.yaml) | `Deployment`, `Service` | Money transfer saga orchestrator |
| [`payment-service.yaml`](file:///c:/Projects/MeshBank/k8s/payment-service.yaml) | `Deployment`, `Service` | Bill & merchant payment workflow with simulated provider |
| [`fraud-notification.yaml`](file:///c:/Projects/MeshBank/k8s/fraud-notification.yaml) | `Deployment`, `Service` | Fraud evaluation and simulated notification delivery services |
| [`nodeports.yaml`](file:///c:/Projects/MeshBank/k8s/nodeports.yaml) | `Service` (NodePort) | Exposes ports 30001–30006 for direct host access |
| [`kind-cluster.yaml`](file:///c:/Projects/MeshBank/k8s/kind-cluster.yaml) | `Cluster` (Kind) | Single-node Kind cluster definition mapping host ports 30001–30006 |

---

## Security & Operational Features

1. **Least Privilege Security Context:**
   - Pods run as non-root user `10001:10001`.
   - `readOnlyRootFilesystem: true` with ephemeral `emptyDir` mounts for `/tmp` and `/data`.
   - `allowPrivilegeEscalation: false` and `capabilities.drop: [ALL]`.
2. **Health & Readiness Probes:**
   - **Liveness:** `GET /health` ensures process liveness.
   - **Readiness:** `GET /ready` checks PostgreSQL connectivity and schema readiness.
3. **Automated Schema Migrations:**
   - On startup, each service inspects and applies Alembic migrations via `DB_MIGRATION_MODE="upgrade"`.
4. **Zero Shared Database Credentials:**
   - Each microservice has an isolated database (`meshbank_auth`, `meshbank_account`, etc.) and distinct PostgreSQL role.
5. **Decentralized JWT Validation:**
   - Public keys (`dev-local-1.pem`) mounted into `/run/meshbank/jwt-public` across all services.
   - Private key mounted **only** in `auth-service` with restrictive mode `0400`.

---

## Quick Start

### 1. Prerequisites
- Docker Desktop (running Linux containers)
- `kubectl`
- `kind` (v0.20+)
- PowerShell (pwsh) or GNU Make

### 2. Deploy Everything
Run the automated deployment script from the repository root:
```powershell
.\k8s\deploy.ps1
```
Or with Make:
```bash
make k8s-up
```

This will:
1. Create the Kind cluster with host port mappings.
2. Load all 6 microservice Docker images + PostgreSQL into the cluster nodes.
3. Apply namespace, ConfigMaps, and inject RS256 JWT keys as Secrets.
4. Deploy PostgreSQL and wait for readiness.
5. Deploy all 6 microservices and NodePort services.
6. Wait for all rollouts to complete.

### 3. Verify Deployment
Check pod and service status:
```bash
kubectl get pods,svc -n meshbank
```

Run the end-to-end Kubernetes smoke test:
```powershell
.\.venv\Scripts\python.exe k8s\k8s-smoke-test.py
```
Or with Make:
```bash
make k8s-test
```

### 4. Tear Down
```powershell
.\k8s\delete.ps1
```
Or:
```bash
make k8s-down
```
