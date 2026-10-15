#!/usr/bin/env python3
"""MeshBank service mesh security tests (run against the kind cluster).

Proves, for every meshed service:
1. Internal endpoints on the public plaintext port return 403 MTLS_REQUIRED.
2. The mTLS port rejects a client without a certificate.
3. The mTLS port rejects a certificate from an untrusted CA, even one that
   claims a trusted service's name.
4. A trusted certificate whose identity is not an authorized caller gets 403
   IDENTITY_NOT_AUTHORIZED.
5. An authorized caller's certificate succeeds.
Then runs the banking smoke test (all hops mTLS) and checks the sidecar logs
show verified, authorized mTLS callers on each receiving service.

With MESHBANK_TOPOLOGY=multi it also attacks each cluster's east-west gateway
from the host: only exported services are routable, and the receiving sidecar
still rejects untrusted and unauthorized certificates that come through it.
It also checks that cross-cluster callers show up in the receivers' logs.

Usage: make k8s-mtls-test   |   make k8s-multi-mtls-test
"""

from __future__ import annotations

import json
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
CERTS_DIR = ROOT_DIR / "k8s" / "certs"
sys.path.insert(0, str(ROOT_DIR / "scripts"))
from mesh_topology import (  # noqa: E402
    CLUSTERS,
    MULTI,
    WORKLOADS,
    exported,
    kube_context,
    topology,
)

CA_CERT = CERTS_DIR / "ca.crt"
ATTACKER = (CERTS_DIR / "attacker.crt", CERTS_DIR / "attacker.key")
NODE_PORTS = {name: 30000 + w.public_port % 100 for name, w in WORKLOADS.items()}

# (service, method, path) that must never be served over plaintext.
PLAINTEXT_PROBES = [
    ("account-service", "GET", "/internal/accounts/00000000-0000-0000-0000-000000000000"),
    ("account-service", "POST", "/internal/dev/accounts/00000000-0000-0000-0000-000000000000/seed"),
    ("transaction-service", "POST", "/internal/payment-debits"),
    ("payment-service", "POST", "/internal/recovery/run"),
    ("fraud-service", "POST", "/evaluate"),
    ("notification-service", "POST", "/notifications"),
]

# Gateway NodePort 30443 is mapped to these host ports (k8s/multicluster/kind-*.yaml).
GATEWAY_HOST_PORTS = {"aws": 31443, "gcp": 32443}

PASSED = 0
FAILED = 0


def report(name: str, ok: bool, details: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
    else:
        FAILED += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    for line in details.strip().splitlines():
        print(f"         {line}")


def run_cmd(cmd: list[str]) -> tuple[int, str, str]:
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def pair(service: str) -> tuple[Path, Path]:
    return CERTS_DIR / f"{service}.crt", CERTS_DIR / f"{service}.key"


def https_get(
    port: int, hostname: str, path: str, cert: tuple[Path, Path] | None
) -> tuple[int | None, str]:
    """Raw HTTPS GET that verifies the server against the mesh CA."""
    ctx = ssl.create_default_context(cafile=str(CA_CERT))
    if cert:
        ctx.load_cert_chain(certfile=str(cert[0]), keyfile=str(cert[1]))
    try:
        with (
            socket.create_connection(("127.0.0.1", port), timeout=5.0) as sock,
            ctx.wrap_socket(sock, server_hostname=hostname) as tls,
        ):
            tls.sendall(
                f"GET {path} HTTP/1.1\r\nHost: {hostname}\r\nConnection: close\r\n\r\n".encode()
            )
            data = b""
            while chunk := tls.recv(4096):
                data += chunk
    except (ssl.SSLError, OSError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    text = data.decode(errors="replace")
    parts = text.split(maxsplit=2)
    status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    return status, text


def rejected_at_tls(status: int | None, body: str) -> bool:
    """NGINX answers a missing/untrusted client cert with 400, or the handshake fails."""
    return status is None or (status == 400 and "SSL certificate" in body)


# payment-service's own Service points at the canary router (no 8443), so the
# mTLS port is tested on the stable backend Service.
MTLS_SERVICE = {"payment-service": "payment-service-stable"}


class PortForward:
    def __init__(self, service: str, local_port: int) -> None:
        self.local_port = local_port
        target = MTLS_SERVICE.get(service, service)
        self.proc = subprocess.Popen(
            [
                "kubectl",
                "--context",
                kube_context(service),
                "port-forward",
                f"svc/{target}",
                "-n",
                "meshbank",
                f"{local_port}:8443",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def __enter__(self) -> PortForward:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", self.local_port), timeout=1).close()
                return self
            except OSError:
                time.sleep(0.3)
        raise RuntimeError(f"port-forward to {self.local_port} did not come up")

    def __exit__(self, *exc: object) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def test_artifacts() -> bool:
    print("\n--- 1. Certificate artifacts ---")
    missing = [
        p.name
        for p in (CA_CERT, *ATTACKER, *(f for w in WORKLOADS for f in pair(w)))
        if not p.exists()
    ]
    report("mesh CA, 6 workload certs and attacker cert exist", not missing, ", ".join(missing))
    return not missing


def test_plaintext_bypass() -> None:
    print("\n--- 2. Plaintext bypass of internal endpoints (NodePorts) ---")
    for service, method, path in PLAINTEXT_PROBES:
        url = f"http://localhost:{NODE_PORTS[service]}{path}"
        try:
            r = httpx.request(method, url, json={}, timeout=5.0)
            ok = r.status_code == 403 and "MTLS_REQUIRED" in r.text
            detail = f"{r.status_code} {r.text.strip()[:120]}"
        except httpx.HTTPError as exc:
            ok, detail = False, str(exc)
        report(f"{service}: {method} {path} over plaintext is refused", ok, detail)


def test_mtls_port(service: str, local_port: int) -> None:
    w = WORKLOADS[service]
    unauthorized = next((s for s in WORKLOADS if s not in w.callers and s != service), None)
    with PortForward(service, local_port):
        status, body = https_get(local_port, w.fqdn, "/health", None)
        report(
            f"{service}: no client certificate is rejected",
            rejected_at_tls(status, body),
            body.splitlines()[0] if body else "",
        )

        status, body = https_get(local_port, w.fqdn, "/health", ATTACKER)
        report(
            f"{service}: untrusted-CA certificate is rejected",
            rejected_at_tls(status, body),
            body.splitlines()[0] if body else "",
        )

        if unauthorized:
            status, body = https_get(local_port, w.fqdn, "/health", pair(unauthorized))
            report(
                f"{service}: trusted but unauthorized identity ({unauthorized}) gets 403",
                status == 403 and "IDENTITY_NOT_AUTHORIZED" in body,
                f"status {status}",
            )

        for caller in w.callers:
            status, body = https_get(local_port, w.fqdn, "/health", pair(caller))
            report(
                f"{service}: authorized caller {caller} succeeds",
                status == 200 and '"status":"ok"' in body,
                f"status {status}",
            )


def test_sidecar_logs() -> None:
    print("\n--- 5. Receiving sidecars logged verified, authorized mTLS callers ---")
    for service, w in WORKLOADS.items():
        if not w.callers:
            continue
        label = "app=payment-service-backend" if service == "payment-service" else f"app={service}"
        _, logs, _ = run_cmd(
            [
                "kubectl",
                "--context",
                kube_context(service),
                "logs",
                "-l",
                label,
                "-c",
                "mesh-sidecar",
                "-n",
                "meshbank",
                "--tail=500",
            ]
        )
        seen: dict[str, dict] = {}
        for line in logs.splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                entry.get("server_port") == "8443"
                and entry.get("ssl_client_verify") == "SUCCESS"
                and entry.get("mesh_caller")
                and entry.get("status", 999) < 400
            ):
                seen[entry["mesh_caller"]] = entry
        sample = next(iter(seen.values()), {})
        report(
            f"{service}: mTLS traffic from {sorted(seen) or 'nobody'}",
            bool(seen),
            f"{sample.get('ssl_protocol')} {sample.get('ssl_cipher')}" if sample else "",
        )
        remote = sorted(c for c in seen if WORKLOADS[c].cluster != w.cluster)
        if topology() == MULTI and any(WORKLOADS[c].cluster != w.cluster for c in w.callers):
            report(
                f"{service} ({w.cluster}): cross-cluster mTLS from {remote or 'nobody'}",
                bool(remote),
                "caller certificate verified end-to-end through the east-west gateways",
            )


def test_gateways() -> None:
    print("\n--- 6. East-west gateways (attacked from the host) ---")
    for cluster in CLUSTERS:
        port = GATEWAY_HOST_PORTS[cluster]
        routable = exported(cluster)
        hidden = next(w for w in WORKLOADS.values() if w.cluster == cluster and w not in routable)
        some_caller = next(iter(routable[0].callers))
        # Valid, authorized certificate, but the service is not exported.
        status, body = https_get(port, hidden.fqdn, "/health", pair(some_caller))
        report(
            f"{cluster} gateway: non-exported {hidden.name} is not routable",
            status is None,
            body.splitlines()[0] if body else "",
        )
        for w in routable:
            remote_caller = next(c for c in w.callers if WORKLOADS[c].cluster != cluster)
            status, body = https_get(port, w.fqdn, "/health", pair(remote_caller))
            report(
                f"{cluster} gateway -> {w.name}: {remote_caller} certificate succeeds",
                status == 200 and '"status":"ok"' in body,
                f"status {status}",
            )
            status, body = https_get(port, w.fqdn, "/health", ATTACKER)
            report(
                f"{cluster} gateway -> {w.name}: untrusted certificate rejected by the sidecar",
                rejected_at_tls(status, body),
                body.splitlines()[0] if body else "",
            )
            outsider = next(s for s in WORKLOADS if s not in w.callers and s != w.name)
            status, body = https_get(port, w.fqdn, "/health", pair(outsider))
            report(
                f"{cluster} gateway -> {w.name}: unauthorized {outsider} gets 403",
                status == 403 and "IDENTITY_NOT_AUTHORIZED" in body,
                f"status {status}",
            )


def main() -> int:
    print("=" * 70)
    print(f"  MESHBANK SERVICE MESH & mTLS TEST SUITE ({topology()} cluster topology)")
    print("=" * 70)
    if not test_artifacts():
        print("\nRun 'make k8s-up' first (it generates the certificates).")
        return 1

    test_plaintext_bypass()

    print("\n--- 3/4. mTLS port: authentication and caller authorization ---")
    for i, service in enumerate(WORKLOADS):
        try:
            test_mtls_port(service, 18440 + i)
        except RuntimeError as exc:
            report(f"{service}: mTLS port reachable", False, str(exc))

    print("\n--- 4b. End-to-end banking workflows through the mesh ---")
    ret, out, err = run_cmd([sys.executable, str(ROOT_DIR / "k8s" / "k8s-smoke-test.py")])
    report(
        "transfers, payment, notifications over mTLS",
        ret == 0,
        "" if ret == 0 else (out + "\n" + err)[-600:],
    )

    test_sidecar_logs()
    if topology() == MULTI:
        test_gateways()

    ret, metrics, _ = run_cmd(
        [
            "kubectl",
            "--context",
            kube_context("transaction-service"),
            "exec",
            "deployment/transaction-service",
            "-c",
            "mesh-sidecar",
            "-n",
            "meshbank",
            "--",
            "wget",
            "-qO-",
            "http://127.0.0.1:9102/metrics",
        ]
    )
    report("sidecar stub_status metrics on :9102", ret == 0 and "Active connections" in metrics)

    print("\n" + "=" * 70)
    print(f"  SUMMARY: {PASSED} passed, {FAILED} failed")
    print("=" * 70)
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
