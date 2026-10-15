# MeshBank Kubernetes architecture (Phase 2D–2G plan)

Status: the two-cluster layout below is **implemented locally** with kind
(`make k8s-multi-up`, README section 18). Differences from this plan: the
east-west gateway is NGINX TLS passthrough (`ssl_preread`), not Envoy, and the
remote-service aliases are one `<cluster>-gateway` Service per remote cluster,
with SNI selecting the service. Real EKS/GKE deployment is not implemented. Nothing below may be
executed until `make preflight PREFLIGHT_ARGS="--profile kind"` passes: Docker
Engine reachable, `kind` installed, and at least 20 GiB free on the host disk.

## 1. Simulation versus real clouds

The two clusters are **local `kind` clusters** whose names and labels *simulate*
two cloud providers. They run as Docker containers on one laptop and share one
host network. They are **not** AWS EKS or Google GKE, and they provide none of
the following:

| Concern | Local kind simulation | Real EKS / GKE (future) |
|---|---|---|
| Network between clusters | Shared Docker bridge network `kind`; nodes reach each other directly | Separate VPCs; needs VPN/Interconnect or public, TLS-protected gateways |
| Load balancers | None built in; NodePort or a local LB (e.g. cloud-provider-kind) | Cloud load balancers (NLB / GCLB) |
| Identity | Kubernetes service accounts only | IAM Roles for Service Accounts / GKE Workload Identity |
| Secrets | Kubernetes Secrets (base64, not encrypted at rest by default) | KMS-encrypted Secrets, AWS Secrets Manager / GCP Secret Manager |
| Failure domains | One machine: a laptop sleep kills both "clouds" | Independent regions and providers |

Every manifest is written so that only the gateway addresses, storage class,
and secret source change when moving to real clouds.

## 2. Placement

| Cluster | kind name / context | Simulates | Services | Data |
|---|---|---|---|---|
| A | `meshbank-aws` / `kind-meshbank-aws` | AWS | auth-service, account-service, transaction-service | PostgreSQL in cluster A (one database and role per service) |
| B | `meshbank-gcp` / `kind-meshbank-gcp` | GCP | payment-service, fraud-service, notification-service | PostgreSQL in cluster B (one database and role per service) |

Each service keeps its own database; no database is shared across clusters.
The two clusters get non-overlapping pod and service CIDRs (A: pods
`10.10.0.0/16`, services `10.11.0.0/16`; B: pods `10.20.0.0/16`, services
`10.21.0.0/16`) so addresses are unambiguous in logs, policies, and any later
flat-network experiment.

## 3. Traffic that crosses clusters

The services call each other synchronously over HTTP:

| Caller | Callee | Direction |
|---|---|---|
| transaction-service (A) | account-service (A) | in-cluster |
| transaction-service (A) | fraud-service (B), notification-service (B) | **A → B** |
| payment-service (B) | fraud-service (B), notification-service (B) | in-cluster |
| payment-service (B) | account-service (A), transaction-service (A) | **B → A** |
| clients | auth, account, transaction (A); payment, notification (B) | ingress |

**Clusters do not discover each other automatically.** Kubernetes DNS
(`*.svc.cluster.local`) resolves only inside its own cluster. Cross-cluster
calls are therefore wired explicitly:

1. Each cluster runs one **east-west gateway** Deployment (Envoy in Phase 2D,
   replaced by the mesh gateway in Phase 2E). It is the *only* workload exposed
   to the other cluster, via a fixed NodePort on the kind node (later a cloud
   load balancer).
2. Each cluster defines **local Service aliases** for remote services, e.g. in
   cluster A a `fraud-service` Service with no selector and an EndpointSlice
   pointing to cluster B's gateway address. Application config is unchanged:
   `FRAUD_SERVICE_URL=https://fraud-service.meshbank.svc:8443`.
3. The gateway routes by SNI or host name to exactly the exposed remote
   services: A exposes `account-service` and `transaction-service` internal
   routes; B exposes `fraud-service` and `notification-service`. Nothing else
   is reachable cross-cluster (auth is never called service-to-service).
4. The gateway node addresses are discovered once by a setup script
   (`docker inspect` of the kind control-plane containers) and written into the
   EndpointSlices. They are not hard-coded in manifests.

## 4. Transport security

* **Phase 2D (before the mesh):** TLS at the gateways. A small private CA
  (cert-manager `CA` issuer, one per environment; the CA key never leaves the
  machine that created it) issues server certificates for each gateway.
  Gateways require **client certificates** from the peer gateway (gateway
  mTLS), so a process that can merely reach the NodePort cannot call internal
  routes. In-cluster pod traffic is still plain HTTP, restricted by
  NetworkPolicy.
* **Phase 2E (service mesh):** pod-to-pod mTLS everywhere with SPIFFE-style
  identities (`spiffe://meshbank/ns/meshbank/sa/transaction-service`). A shared
  trust root spans both clusters, with an intermediate CA per cluster.
  Authorization policy then names caller identities, and the shared
  `INTERNAL_API_TOKEN` is retired.
* Customer JWTs stay end-to-end: verified by every service with RS256 public
  keys, regardless of the transport layer.

## 5. Secrets and keys

| Secret | Cluster A | Cluster B | Notes |
|---|---|---|---|
| JWT **private** signing key | Secret, mounted only into auth-service (`defaultMode: 0400`, `fsGroup: 10001`) | **absent** | Production kid, never `dev-*` (startup rejects it) |
| JWT public keys (`<kid>.pem`) | ConfigMap, mounted into every service | Same ConfigMap content | Rotation: add new public key to both clusters first, then switch the signing kid, then remove the old key after `ACCESS_TOKEN_TTL_SECONDS` |
| `INTERNAL_API_TOKEN` | Secret | Same value, separate Secret | Interim until mesh identities (Phase 2E) |
| Database credentials | Per-service Secret | Per-service Secret | One PostgreSQL role per service, least privilege |
| Gateway TLS keys | cert-manager Secret | cert-manager Secret | Never copied between clusters; only the CA certificate is shared |

Secrets are created by a setup script from local files that are **not**
committed (`secrets/` is git-ignored, excluded from Docker build contexts) and
are never printed to logs.

## 6. Network policies

kind's default CNI (kindnet) **does not enforce NetworkPolicy**. Both clusters
are created with `disableDefaultCNI: true` and Calico is installed, otherwise
the policies below would silently do nothing. A test pod verifies enforcement
before workloads are deployed.

* Default deny of all ingress and egress in namespace `meshbank`.
* Allow DNS egress to kube-dns only.
* Allow each service's ingress only from its known callers (table in §3) and
  from the gateway for cross-cluster callers.
* Allow each service's egress only to its own PostgreSQL and its callees (or
  the local gateway for remote callees).
* PostgreSQL accepts connections only from its owning services.

## 7. Workload configuration

* Containers run as uid 10001, `runAsNonRoot`, `readOnlyRootFilesystem`,
  `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`,
  `seccompProfile: RuntimeDefault`; writable `emptyDir` only for `/tmp`.
* Requests and limits mirror Compose (auth 512 MiB, others 256 MiB).
* **Migrations:** a Kubernetes Job per service runs `alembic upgrade head`
  against PostgreSQL before the Deployment rolls out; services run with
  `DB_MIGRATION_MODE=check` and refuse to start on schema drift.
* **Probes:** `startupProbe` and `livenessProbe` on `/health`;
  `readinessProbe` on `/ready` (database reachable). Readiness never depends on
  the *other* cluster, so a cross-cluster outage does not cascade into restarts.
* `PodDisruptionBudget` with `minAvailable: 1` once replicas > 1.

## 8. Failure and recovery behaviour

* **Cross-cluster link down:** callers receive connection errors or timeouts
  (`HTTP_TIMEOUT_SECONDS`) and return `503 *_UNAVAILABLE`. Transfers in
  progress stay in their durable saga state; the transaction and payment
  recovery loops (`RECOVERY_INTERVAL_SECONDS`) resume or compensate them when
  the link returns. Retries are limited to idempotent calls.
* **One cluster lost:** the other cluster keeps serving what it owns (e.g.
  cluster A still authenticates and serves balances); flows needing the lost
  side fail fast with 503. No automatic failover of data between clusters is
  attempted in this phase.
* **Gateway restart:** short 503 window, absorbed by client retries on
  idempotent calls and by saga recovery for the rest.
* Planned chaos tests (Phase 2F): block the gateway NodePort, kill a gateway
  pod, restart a PostgreSQL pod, and verify balances reconcile.

## 9. Resource budget and gates

| Step | Gate | Approximate disk |
|---|---|---|
| Build six images | `--profile docker` | 10 GiB free |
| Trivy scan | `--profile scan` | +1–2 GiB vulnerability DB |
| Two kind clusters + Calico + PostgreSQL | `--profile kind` | 20 GiB free |
| Monitoring (Prometheus/Grafana) | `--profile kind`, re-run after clusters are up | +3–5 GiB |

Each gate is read-only and fails with instructions; it never cleans up Docker
or deletes files.
