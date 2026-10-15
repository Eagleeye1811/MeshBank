#!/usr/bin/env python3
"""MeshBank Service Mesh Verification and Security Test Suite.

Proves:
1. Workload mutual TLS (mTLS) authentication between payment and transaction services.
2. Strict rejection of connections with missing client certificates (HTTP 400 SSL No Cert).
3. Strict rejection of connections with untrusted/attacker certificates (HTTP 400 SSL Error).
4. Strict rejection of plaintext bypass attempts for internal service endpoints (HTTP 403 MTLS_REQUIRED).
5. In-cluster sidecar mediation for complete banking payment workflows.
6. Extraction and verification of structured sidecar proxy access logs and metrics.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
CERTS_DIR = ROOT_DIR / "k8s" / "certs"

CA_CERT = str(CERTS_DIR / "ca.crt")
PAYMENT_CERT = str(CERTS_DIR / "payment-service.crt")
PAYMENT_KEY = str(CERTS_DIR / "payment-service.key")
TRANSACTION_CERT = str(CERTS_DIR / "transaction-service.crt")
TRANSACTION_KEY = str(CERTS_DIR / "transaction-service.key")
ATTACKER_CERT = str(CERTS_DIR / "attacker.crt")
ATTACKER_KEY = str(CERTS_DIR / "attacker.key")

PASS_COUNT = 0
FAIL_COUNT = 0


def report(test_name: str, passed: bool, details: str = "") -> None:
    global PASS_COUNT, FAIL_COUNT
    if passed:
        PASS_COUNT += 1
        print(f"  [PASS] {test_name}")
    else:
        FAIL_COUNT += 1
        print(f"  [FAIL] {test_name}")
    if details:
        for line in details.strip().splitlines():
            print(f"         {line}")


def run_cmd(cmd: list[str]) -> tuple[int, str, str]:
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def send_raw_https(
    port: int,
    hostname: str,
    path: str,
    cafile: str,
    certfile: str | None = None,
    keyfile: str | None = None,
) -> tuple[int | None, str]:
    ctx = ssl.create_default_context(cafile=cafile)
    if certfile and keyfile:
        ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5.0) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                req = f"GET {path} HTTP/1.1\r\nHost: {hostname}\r\nConnection: close\r\n\r\n"
                ssock.sendall(req.encode("utf-8"))
                response_data = b""
                while True:
                    chunk = ssock.recv(4096)
                    if not chunk:
                        break
                    response_data += chunk
                resp_text = response_data.decode("utf-8", errors="replace")
                status = None
                if resp_text.startswith("HTTP/"):
                    parts = resp_text.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        status = int(parts[1])
                return status, resp_text
    except ssl.SSLError as exc:
        return None, f"SSLError: {exc}"
    except Exception as exc:
        return None, f"ConnectionError: {exc}"


def main() -> int:
    print("=" * 70)
    print("  MESHBANK SERVICE MESH & mTLS COMPLIANCE TEST SUITE")
    print("=" * 70)

    # 1. Verify certificate files exist
    print("\n--- Phase 1.1: Certificate & Workload Identity Artifacts ---")
    for f_path, label in [
        (CA_CERT, "Mesh Root CA"),
        (PAYMENT_CERT, "payment-service client certificate"),
        (PAYMENT_KEY, "payment-service private key"),
        (TRANSACTION_CERT, "transaction-service certificate"),
        (TRANSACTION_KEY, "transaction-service private key"),
        (ATTACKER_CERT, "Attacker rogue certificate"),
        (ATTACKER_KEY, "Attacker rogue key"),
    ]:
        exists = os.path.exists(f_path)
        report(f"Artifact {label} exists", exists, f"Path: {f_path}")

    # 2. Test Plaintext Bypass Rejection
    print("\n--- Phase 1.2: Plaintext Bypass Rejection (Network Restriction) ---")
    url_tx_internal = "http://localhost:30003/internal/payment-debits"
    try:
        r = httpx.post(url_tx_internal, timeout=3.0)
        is_rejected = r.status_code == 403 and "MTLS_REQUIRED" in r.text
        report(
            "Plaintext request to transaction-service /internal/ is blocked",
            is_rejected,
            f"Status: {r.status_code}, Body: {r.text.strip()}",
        )
    except Exception as exc:
        report("Plaintext request to transaction-service /internal/ is blocked", False, str(exc))

    url_pay_internal = "http://localhost:30004/internal/payment-debits"
    try:
        r = httpx.post(url_pay_internal, timeout=3.0)
        is_rejected = r.status_code == 403 and "MTLS_REQUIRED" in r.text
        report(
            "Plaintext request to payment-service /internal/ is blocked",
            is_rejected,
            f"Status: {r.status_code}, Body: {r.text.strip()}",
        )
    except Exception as exc:
        report("Plaintext request to payment-service /internal/ is blocked", False, str(exc))

    # 3. Test Direct mTLS Handshakes via port-forward
    print("\n--- Phase 1.3: Direct mTLS Handshake & Identity Verification ---")
    pf_proc = subprocess.Popen(
        ["kubectl", "port-forward", "svc/transaction-service", "-n", "meshbank", "18443:8443"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(2)  # Allow port-forward to establish

    hostname = "transaction-service.meshbank.svc.cluster.local"
    try:
        # 3a. Missing Client Certificate -> MUST BE REJECTED
        status, body = send_raw_https(
            port=18443,
            hostname=hostname,
            path="/health",
            cafile=CA_CERT,
            certfile=None,
            keyfile=None,
        )
        rejected_no_cert = (status == 400 and "No required SSL certificate was sent" in body) or (status is None and "SSLError" in body)
        report(
            "Request without client certificate is rejected by mTLS sidecar",
            rejected_no_cert,
            f"Status: {status}, Response: {body.splitlines()[0] if body else ''}",
        )

        # 3b. Untrusted Attacker Certificate -> MUST BE REJECTED
        status, body = send_raw_https(
            port=18443,
            hostname=hostname,
            path="/health",
            cafile=CA_CERT,
            certfile=ATTACKER_CERT,
            keyfile=ATTACKER_KEY,
        )
        rejected_attacker = (status == 400 and "The SSL certificate error" in body) or (status is None and "SSLError" in body)
        report(
            "Request with untrusted attacker certificate is rejected by mTLS sidecar",
            rejected_attacker,
            f"Status: {status}, Response: {body.splitlines()[0] if body else ''}",
        )

        # 3c. Valid Workload Certificate (payment-service) -> MUST SUCCEED (200 OK)
        status, body = send_raw_https(
            port=18443,
            hostname=hostname,
            path="/health",
            cafile=CA_CERT,
            certfile=PAYMENT_CERT,
            keyfile=PAYMENT_KEY,
        )
        valid_success = status == 200 and '"status":"ok"' in body
        report(
            "Request with valid payment-service workload certificate succeeds",
            valid_success,
            f"Status: {status}, Body: {body.splitlines()[-1] if body else ''}",
        )

    finally:
        pf_proc.terminate()
        try:
            pf_proc.wait(timeout=2)
        except Exception:
            pf_proc.kill()

    # 4. In-Cluster Sidecar Mediation (Payment -> Transaction Workflow)
    print("\n--- Phase 1.4: In-Cluster Mesh Traffic Mediation ---")
    ret, out, err = run_cmd([sys.executable, str(ROOT_DIR / "k8s" / "k8s-smoke-test.py")])
    report(
        "End-to-End banking transaction & bill payment through mesh proxies",
        ret == 0,
        "All 9 steps (Transfers, bill payments, balance updates) executed through sidecar proxies"
        if ret == 0
        else f"Error: {out[-300:]}",
    )

    # 5. Sidecar Proxy Logs Verification (mTLS cipher, protocol, client DN)
    print("\n--- Phase 1.5: Sidecar Proxy Structured Logs & Metrics ---")
    ret, logs, _ = run_cmd([
        "kubectl", "logs", "-l", "app=transaction-service", "-c", "mesh-sidecar", "-n", "meshbank", "--tail=30"
    ])
    found_mtls_log = False
    verified_entry = {}
    for line in logs.splitlines():
        try:
            data = json.loads(line)
            if (
                data.get("ssl_client_verify") == "SUCCESS"
                and "payment-service" in data.get("ssl_client_s_dn", "")
            ):
                found_mtls_log = True
                verified_entry = data
                break
        except Exception:
            continue

    report(
        "Transaction sidecar captured valid mTLS handshake with payment-service",
        found_mtls_log,
        f"Verified log: Protocol={verified_entry.get('ssl_protocol')}, "
        f"Cipher={verified_entry.get('ssl_cipher')}, "
        f"Client-DN={verified_entry.get('ssl_client_s_dn')}, "
        f"Latency={verified_entry.get('request_time')}s",
    )

    # 6. Proxy Metrics Verification
    ret, metrics_out, _ = run_cmd([
        "kubectl", "exec", "deployment/transaction-service", "-c", "mesh-sidecar", "-n", "meshbank", "--",
        "wget", "-qO-", "http://127.0.0.1:9102/metrics"
    ])
    has_metrics = "Active connections" in metrics_out or "server accepts handled requests" in metrics_out
    report(
        "Sidecar metrics endpoint (port 9102) active and recording stats",
        has_metrics,
        metrics_out.strip(),
    )

    print("\n" + "=" * 70)
    print(f"  TEST SUMMARY: {PASS_COUNT} PASSED, {FAIL_COUNT} FAILED")
    print("=" * 70)
    return 0 if FAIL_COUNT == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
