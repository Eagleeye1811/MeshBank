"""Single source of truth for the MeshBank service mesh.

Used by ``generate_mesh_certs.py`` (one workload certificate per service) and
``generate_mesh_sidecars.py`` (one NGINX sidecar config per deployment). Change
the call graph here, regenerate, and both stay consistent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

NAMESPACE = "meshbank"
TRUST_DOMAIN = "meshbank.internal"
MTLS_PORT = 8443
METRICS_PORT = 9102
# Every application binds here; only its own sidecar can reach it.
APP_LOOPBACK = "127.0.0.1:8080"

# Multi-cluster mode: two clusters standing in for two clouds. Each runs an
# east-west gateway that routes TLS by SNI without terminating it, so mTLS
# stays end-to-end between sidecars. Sidecars reach the other cluster's
# gateway through a local alias Service "<cluster>-gateway".
CLUSTERS = ("aws", "gcp")
GATEWAY_PORT = 15443  # gateway pod port and alias Service port
GATEWAY_NODE_PORT = 30443  # gateway NodePort on each cluster's nodes
# MESHBANK_TOPOLOGY selects which deployment the ops scripts talk to.
SINGLE, MULTI = "single", "multi"


@dataclass(frozen=True)
class Workload:
    name: str  # Kubernetes Service / workload name, e.g. "account-service"
    public_port: int  # plaintext port the sidecar serves (NodePort, router, probes)
    cluster: str  # multi-cluster placement: "aws" or "gcp"
    callers: tuple[str, ...] = ()  # workloads authorized on the mTLS port
    upstreams: tuple[str, ...] = ()  # workloads this one calls (egress listeners)
    # Internal-only endpoints that do not live under /internal/ (exact paths).
    internal_paths: tuple[str, ...] = ()
    # False: the public port serves only /health and /ready (fully internal service).
    public_api: bool = True

    @property
    def short(self) -> str:
        return self.name.removesuffix("-service")

    @property
    def fqdn(self) -> str:
        return f"{self.name}.{NAMESPACE}.svc.cluster.local"

    @property
    def spiffe_id(self) -> str:
        return f"spiffe://{TRUST_DOMAIN}/ns/{NAMESPACE}/sa/{self.name}"

    @property
    def cert_secret(self) -> str:
        return f"{self.short}-mesh-certs"

    @property
    def egress_port(self) -> int:
        """Loopback port other workloads' apps use to reach this one, e.g. 9002."""
        return 9000 + self.public_port % 100


WORKLOADS: dict[str, Workload] = {
    w.name: w
    for w in (
        Workload("auth-service", 8001, "aws"),
        Workload(
            "account-service", 8002, "aws", callers=("transaction-service", "payment-service")
        ),
        Workload(
            "transaction-service",
            8003,
            "aws",
            callers=("payment-service",),
            upstreams=("account-service", "fraud-service", "notification-service"),
        ),
        Workload(
            "payment-service",
            8004,
            "gcp",
            upstreams=(
                "account-service",
                "transaction-service",
                "fraud-service",
                "notification-service",
            ),
        ),
        Workload(
            "fraud-service",
            8005,
            "gcp",
            callers=("transaction-service", "payment-service"),
            public_api=False,
        ),
        Workload(
            "notification-service",
            8006,
            "gcp",
            callers=("transaction-service", "payment-service"),
            internal_paths=("/notifications",),
        ),
    )
}


@dataclass(frozen=True)
class Deployment:
    """One sidecar config: a workload, optionally on a release track."""

    workload: Workload
    config_name: str
    track: str | None = None


DEPLOYMENTS: tuple[Deployment, ...] = (
    *(
        Deployment(w, f"{w.short}-sidecar-config")
        for w in WORKLOADS.values()
        if w.name != "payment-service"
    ),
    Deployment(WORKLOADS["payment-service"], "payment-sidecar-config", track="stable"),
    Deployment(WORKLOADS["payment-service"], "payment-canary-sidecar-config", track="canary"),
)


def gateway_alias(cluster: str) -> str:
    """Local Service that forwards to the given cluster's east-west gateway."""
    return f"{cluster}-gateway"


def exported(cluster: str) -> list[Workload]:
    """Workloads in ``cluster`` that a workload in another cluster calls."""
    return [
        w
        for w in WORKLOADS.values()
        if w.cluster == cluster and any(WORKLOADS[c].cluster != cluster for c in w.callers)
    ]


def topology() -> str:
    value = os.environ.get("MESHBANK_TOPOLOGY", SINGLE)
    if value not in (SINGLE, MULTI):
        raise ValueError(f"MESHBANK_TOPOLOGY must be '{SINGLE}' or '{MULTI}', not {value!r}")
    return value


def kube_context(workload: str) -> str:
    """kubectl context of the kind cluster running ``workload``."""
    if topology() == MULTI:
        return f"kind-meshbank-{WORKLOADS[workload].cluster}"
    return "kind-meshbank"


def validate() -> None:
    for w in WORKLOADS.values():
        if w.cluster not in CLUSTERS:
            raise ValueError(f"{w.name}: unknown cluster {w.cluster}")
        for other in (*w.callers, *w.upstreams):
            if other not in WORKLOADS:
                raise ValueError(f"{w.name}: unknown workload {other}")
        for up in w.upstreams:
            if w.name not in WORKLOADS[up].callers:
                raise ValueError(f"{w.name} calls {up}, but {up} does not authorize it")


validate()
