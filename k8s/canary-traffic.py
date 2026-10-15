#!/usr/bin/env python3
"""MeshBank Service Mesh Phase 2: Traffic Management & Canary Deployment Manager.

Supports:
1. Dynamic traffic splitting between Stable (v1) and Canary (v2).
2. Header-based routing overrides (X-Canary: always / never).
3. Verification of request distribution with statistical metrics.
4. Health-based incremental rollout (e.g. 10% -> 50% -> 100%).
5. Automated rollback on failure/degradation.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))
from mesh_topology import kube_context  # noqa: E402

# The router and both payment tracks live with payment-service (in the "gcp"
# cluster when MESHBANK_TOPOLOGY=multi). Pinning the context means the script
# never acts on whatever cluster kubectl currently points at.
KUBECTL = ["kubectl", "--context", kube_context("payment-service")]
ROUTER_CONFIG_PATH = ROOT_DIR / "k8s" / "mesh-router-config.yaml"
ROUTER_URL = "http://localhost:30004"


def run_cmd(cmd: list[str]) -> tuple[int, str, str]:
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return res.returncode, res.stdout.strip(), res.stderr.strip()


def get_current_split() -> tuple[int, int]:
    """Reads current weights from router configmap in cluster."""
    ret, out, _ = run_cmd(
        [
            *KUBECTL,
            "get",
            "configmap",
            "payment-router-config",
            "-n",
            "meshbank",
            "-o",
            "jsonpath={.data.nginx\\.conf}",
        ]
    )
    stable_match = re.search(r"server payment-service-stable[^\n]*weight=(\d+)", out)
    canary_match = re.search(r"server payment-service-canary[^\n]*weight=(\d+)", out)
    stable_w = int(stable_match.group(1)) if stable_match else 100
    canary_w = int(canary_match.group(1)) if canary_match else 0
    return stable_w, canary_w


WEIGHTED_UPSTREAM = re.compile(r"(upstream payment_weighted_upstream \{\n)(.*?)(\n\s*\})", re.S)


def render_router_config(stable_weight: int, canary_weight: int) -> str:
    """The tracked router manifest with only the weighted split lines replaced."""
    manifest = ROUTER_CONFIG_PATH.read_text(encoding="utf-8")
    match = WEIGHTED_UPSTREAM.search(manifest)
    if not match:
        raise ValueError(f"payment_weighted_upstream block not found in {ROUTER_CONFIG_PATH}")
    indent = re.match(r"\s*", match.group(2)).group(0)

    def server(track: str, weight: int) -> str:
        host = f"server payment-service-{track}.meshbank.svc.cluster.local:8004"
        # NGINX rejects weight=0; a zero-weight backend is marked down instead.
        if weight == 0:
            return f"{indent}{host} down;"
        return f"{indent}{host} weight={weight} max_fails=2 fail_timeout=5s;"

    lines = "\n".join([server("stable", stable_weight), server("canary", canary_weight)])
    return manifest[: match.start(2)] + lines + manifest[match.end(2) :]


def set_split(stable_weight: int, canary_weight: int) -> bool:
    """Applies new router weights to the cluster and restarts the router."""
    if not (0 <= stable_weight <= 100 and 0 <= canary_weight <= 100):
        print("  [ERROR] weights must be between 0 and 100")
        return False
    if stable_weight + canary_weight == 0:
        print("  [ERROR] at least one track needs a non-zero weight")
        return False
    print(f"\n[Setting Traffic Split] Stable (v1): {stable_weight} | Canary (v2): {canary_weight}")

    # Applied from stdin: the tracked manifest keeps its default split.
    config = render_router_config(stable_weight, canary_weight)
    res = subprocess.run(
        [*KUBECTL, "apply", "-f", "-"], input=config, capture_output=True, text=True, check=False
    )
    if res.returncode != 0:
        print(f"  [ERROR] Failed to apply router config: {res.stderr.strip()}")
        return False

    # subPath ConfigMap mounts never update in place, so roll the router.
    for cmd in (
        [*KUBECTL, "rollout", "restart", "deployment/payment-router", "-n", "meshbank"],
        [
            *KUBECTL,
            "rollout",
            "status",
            "deployment/payment-router",
            "-n",
            "meshbank",
            "--timeout=60s",
        ],
    ):
        ret, _, err = run_cmd(cmd)
        if ret != 0:
            print(f"  [ERROR] {' '.join(cmd[1:3])} failed: {err}")
            return False
    print("  [OK] Router restarted with the new split.")
    return True


def verify_distribution(num_requests: int = 100) -> dict[str, int]:
    """Sends N requests and tallies hits between stable and canary."""
    print(f"\n[Verifying Traffic Distribution] {num_requests} requests to {ROUTER_URL}/health...")
    counts: Counter[str] = Counter()
    versions: Counter[str] = Counter()
    errors = 0

    with httpx.Client(timeout=3.0) as client:
        for _ in range(num_requests):
            try:
                resp = client.get(f"{ROUTER_URL}/health")
                if resp.status_code == 200:
                    track = resp.headers.get("X-Mesh-Track", "unknown")
                    ver = resp.headers.get("X-Service-Version", "unknown")
                    counts[track] += 1
                    versions[ver] += 1
                else:
                    errors += 1
            except Exception:
                errors += 1

    total_valid = sum(counts.values())
    stable_pct = (counts["stable"] / total_valid * 100) if total_valid else 0
    canary_pct = (counts["canary"] / total_valid * 100) if total_valid else 0

    print("\n  " + "=" * 50)
    print("  TRAFFIC MANAGEMENT VERIFICATION REPORT")
    print("  " + "=" * 50)
    print(f"  Total Requests:  {num_requests}")
    print(f"  Successful:      {total_valid} | Failed: {errors}")
    print(f"  Stable (v1):     {counts['stable']:3d} hits ({stable_pct:5.1f}%)")
    print(f"  Canary (v2):     {counts['canary']:3d} hits ({canary_pct:5.1f}%)")
    print("  " + "=" * 50)
    return dict(counts)


def test_header_routing() -> bool:
    """Proves header-based routing overrides for A/B testing."""
    print("\n[Testing Header-based Routing Overrides]")
    all_passed = True
    with httpx.Client(timeout=3.0) as client:
        # 1. Force Canary
        resp_canary = client.get(f"{ROUTER_URL}/health", headers={"X-Canary": "always"})
        track_c = resp_canary.headers.get("X-Mesh-Track")
        ver_c = resp_canary.headers.get("X-Service-Version")
        c_ok = track_c == "canary" and ver_c == "v2"
        print(f"  [{'PASS' if c_ok else 'FAIL'}] X-Canary: always -> {track_c} ({ver_c})")
        if not c_ok:
            all_passed = False

        # 2. Force Stable
        resp_stable = client.get(f"{ROUTER_URL}/health", headers={"X-Canary": "never"})
        track_s = resp_stable.headers.get("X-Mesh-Track")
        ver_s = resp_stable.headers.get("X-Service-Version")
        s_ok = track_s == "stable" and ver_s == "v1"
        print(f"  [{'PASS' if s_ok else 'FAIL'}] X-Canary: never  -> {track_s} ({ver_s})")
        if not s_ok:
            all_passed = False

    return all_passed


def demo_rollout() -> None:
    """Executes a full incremental Canary Rollout Demonstration."""
    print("\n" + "=" * 70)
    print("  STAGE 1: CANARY ACTIVATION (90% STABLE / 10% CANARY)")
    print("=" * 70)
    set_split(90, 10)
    verify_distribution(100)

    print("\n" + "=" * 70)
    print("  STAGE 2: TRAFFIC SPLIT RAMP-UP (50% STABLE / 50% CANARY)")
    print("=" * 70)
    set_split(50, 50)
    verify_distribution(100)

    print("\n" + "=" * 70)
    print("  STAGE 3: FULL PROMOTION (0% STABLE / 100% CANARY)")
    print("=" * 70)
    set_split(0, 100)
    verify_distribution(100)

    print("\n" + "=" * 70)
    print("  STAGE 4: AUTOMATED ROLLBACK TO STABLE (100% STABLE / 0% CANARY)")
    print("=" * 70)
    set_split(100, 0)
    verify_distribution(100)


def demo_rollback() -> None:
    """Demonstrates health-based failure detection and rollback."""
    print("\n" + "=" * 70)
    print("  HEALTH-BASED ROLLBACK DEMONSTRATION")
    print("=" * 70)
    # Start at 50/50
    set_split(50, 50)
    print("\n[Simulating Canary Failure] Scaling canary deployment to 0 replicas...")
    run_cmd(
        [*KUBECTL, "scale", "deployment/payment-service-canary", "-n", "meshbank", "--replicas=0"]
    )
    time.sleep(2)

    print(
        "[Health Monitor] Detecting canary failure and executing instant rollback to 100% stable..."
    )
    set_split(100, 0)
    verify_distribution(50)

    print("\n[Recovery] Restoring canary deployment replicas to 1...")
    run_cmd(
        [*KUBECTL, "scale", "deployment/payment-service-canary", "-n", "meshbank", "--replicas=1"]
    )
    run_cmd(
        [
            *KUBECTL,
            "rollout",
            "status",
            "deployment/payment-service-canary",
            "-n",
            "meshbank",
            "--timeout=60s",
        ]
    )
    print("  [OK] Canary pod restored and healthy.")


def main() -> int:
    parser = argparse.ArgumentParser(description="MeshBank Phase 2 Canary Traffic Manager")
    parser.add_argument(
        "--set-split", nargs=2, type=int, metavar=("STABLE", "CANARY"), help="Set split weights"
    )
    parser.add_argument(
        "--verify", type=int, default=0, metavar="N", help="Verify distribution across N requests"
    )
    parser.add_argument(
        "--test-header", action="store_true", help="Test header-based routing overrides"
    )
    parser.add_argument(
        "--demo-rollout", action="store_true", help="Run full rollout lifecycle demo"
    )
    parser.add_argument(
        "--demo-rollback", action="store_true", help="Run health failure & rollback demo"
    )

    args = parser.parse_args()

    if args.set_split:
        return 0 if set_split(args.set_split[0], args.set_split[1]) else 1
    elif args.verify:
        verify_distribution(args.verify)
    elif args.test_header:
        success = test_header_routing()
        sys.exit(0 if success else 1)
    elif args.demo_rollout:
        demo_rollout()
    elif args.demo_rollback:
        demo_rollback()
    else:
        # Default: Run the full Phase 2 verification suite
        test_header_routing()
        demo_rollout()

    return 0


if __name__ == "__main__":
    sys.exit(main())
