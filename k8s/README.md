# MeshBank Kubernetes Deployment

Manifests and scripts for running MeshBank on a local **kind** cluster with an
NGINX sidecar service mesh (mTLS between all services) and a canary release of
`payment-service`. Design and security model: main README sections 16-17.

---

## Architecture

```
 host :30001-30006 (NodePorts)
   │
   ▼                         every pod: app on 127.0.0.1:8080 + mesh-sidecar
 auth ─ account ─ transaction ─ fraud ─ notification       (public port 80xx,
   │                                                         mTLS :8443,
   └── payment-router ──▶ payment-service-stable (v1)        egress 127.0.0.1:90xx,
                     └──▶ payment-service-canary (v2)        metrics :9102)

 service ⇄ service: app → own sidecar (loopback) ══mTLS══▶ target sidecar :8443 → app
 PostgreSQL 15 (one database + role per service), reachable only from meshed pods
```

| File | Contents |
|---|---|
| [`namespace.yaml`](namespace.yaml) | `meshbank` namespace |
| [`configmap.yaml`](configmap.yaml) | Shared settings; service URLs point at the local sidecar egress ports |
| [`secrets.yaml`](secrets.yaml) | PostgreSQL password, internal API token (development values) |
| [`postgres.yaml`](postgres.yaml) | PostgreSQL with per-service databases and roles |
| [`auth-service.yaml`](auth-service.yaml), [`account-service.yaml`](account-service.yaml), [`transaction-service.yaml`](transaction-service.yaml), [`fraud-notification.yaml`](fraud-notification.yaml) | Deployments (app + sidecar) and Services |
| [`payment-service.yaml`](payment-service.yaml), [`payment-canary.yaml`](payment-canary.yaml) | Stable and canary payment deployments, their Services, the payment router |
| [`mesh-sidecar-configs.yaml`](mesh-sidecar-configs.yaml) | **Generated** sidecar NGINX configs (`make mesh-configs`) |
| [`mesh-router-config.yaml`](mesh-router-config.yaml) | Canary router NGINX config (default 80/20) |
| [`network-policy-mesh.yaml`](network-policy-mesh.yaml) | mTLS port only from meshed pods; PostgreSQL only from meshed pods |
| [`nodeports.yaml`](nodeports.yaml), [`kind-cluster.yaml`](kind-cluster.yaml) | Host access on ports 30001-30006 |
| `mesh-certs.yaml`, `certs/` | **Generated, git-ignored** mesh CA and workload certificates |

The mesh call graph and caller allow-lists are defined once in
[`../scripts/mesh_topology.py`](../scripts/mesh_topology.py).

## Quick start (macOS / Linux)

Prerequisites: Docker Desktop running, `brew install kind kubectl`
(kind ≥ 0.24 for NetworkPolicy enforcement), and `make install`.

```bash
make k8s-up            # build images (incl. payment v2), create cluster, deploy
make k8s-status
make k8s-test          # banking smoke test: every service hop over mTLS
make k8s-mtls-test     # mesh security tests
make k8s-canary-demo   # 90/10 → 50/50 → 0/100 → 100/0
make k8s-down
```

Options: `SKIP_BUILD=1 make k8s-up` reuses images; `ROTATE_CERTS=1 make k8s-up`
issues a new mesh CA and restarts the pods. Re-running `make k8s-up` is safe:
it keeps the existing certificates, applies changes, and restarts the
deployments so they pick up rebuilt images and configs.

## Two clusters (AWS / GCP simulation)

```bash
make k8s-multi-up        # kind-meshbank-aws + kind-meshbank-gcp, joined by east-west gateways
make k8s-multi-test
make k8s-multi-mtls-test
make k8s-multi-down
```

Files: [`multicluster/kind-aws.yaml`](multicluster/kind-aws.yaml),
[`multicluster/kind-gcp.yaml`](multicluster/kind-gcp.yaml),
`multicluster/mesh-sidecar-configs.yaml` and `multicluster/east-west-gateway.yaml`
(both **generated**). [`deploy-multicluster.sh`](deploy-multicluster.sh) sends
each cluster only its own objects (`scripts/cluster_manifests.py`) and points
each `<cluster>-gateway` alias at the other cluster. Shared steps live in
[`lib.sh`](lib.sh). Design: main README section 18.

The ops scripts take the cluster from `MESHBANK_TOPOLOGY` (`single` by default,
or `multi`) and always pass an explicit `--context`, so they never act on
whatever cluster kubectl currently points at.

## Windows

```powershell
.\k8s\deploy.ps1            # or -SkipBuild
.\k8s\test-mesh-mtls.ps1
.\k8s\canary-traffic.ps1 -Command demo-rollout
.\k8s\delete.ps1
```

## Development-only funding

`/internal/*` is reachable only over mTLS inside the cluster, so seed balances
with the account service's CLI in its pod:

```bash
kubectl exec -n meshbank deployment/account-service -c account-service -- \
  python -m app.dev_seed --account-id <uuid> --amount 5000.00
```

The dashboard's "Add test money" button runs the same command through the Vite
dev server.
