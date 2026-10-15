#!/usr/bin/env bash
# Deploy MeshBank to ONE local kind cluster (macOS / Linux). Idempotent: re-run
# it after code or manifest changes. Two clusters (aws/gcp simulation):
# k8s/deploy-multicluster.sh. Windows users: k8s/deploy.ps1.
#
#   make k8s-up                     # build, create cluster, deploy
#   SKIP_BUILD=1 make k8s-up        # reuse existing images
#   ROTATE_CERTS=1 make k8s-up      # issue a new mesh CA and workload certs
#
# Requires: docker (running), kind >= 0.24 (NetworkPolicy enforcement), kubectl.
set -euo pipefail
# shellcheck source=k8s/lib.sh
source "$(dirname "$0")/lib.sh"

CLUSTER=meshbank
CTX="kind-$CLUSTER"
SERVICES="auth account transaction payment fraud notification"
# Deployments with a mesh sidecar, plus the canary router.
DEPLOYMENTS="auth-service account-service transaction-service payment-service \
payment-service-canary fraud-service notification-service payment-router"

preflight
require_no_cluster meshbank-aws meshbank-gcp
build_images
ensure_cluster "$CLUSTER" k8s/kind-cluster.yaml
kubectl config use-context "$CTX" >/dev/null

IMAGES="postgres:15-alpine nginx:alpine meshbank/payment-service:v2"
for svc in $SERVICES; do IMAGES="$IMAGES meshbank/$svc-service:dev"; done
# shellcheck disable=SC2086  # word splitting intended
load_images "$CLUSTER" $IMAGES

mesh_material
# shellcheck disable=SC2086
existing_deployments $DEPLOYMENTS
apply_shared

selector="$(k get deployment payment-service -n "$NS" \
  -o jsonpath='{.spec.selector.matchLabels.app}' 2>/dev/null || true)"
if [[ "$selector" == "payment-service" ]]; then
  # The selector changed to app=payment-service-backend and is immutable.
  echo "  replacing payment-service deployment (old selector)"
  k delete deployment payment-service -n "$NS" --wait=true
fi

step "Banking services, sidecars, canary"
k apply -f k8s/mesh-certs.yaml -f k8s/mesh-sidecar-configs.yaml -f k8s/mesh-router-config.yaml
k apply -f k8s/auth-service.yaml -f k8s/account-service.yaml \
  -f k8s/fraud-notification.yaml -f k8s/transaction-service.yaml \
  -f k8s/payment-service.yaml -f k8s/payment-canary.yaml -f k8s/nodeports.yaml
restart_existing
# shellcheck disable=SC2086
wait_for $DEPLOYMENTS

k get pods -n "$NS"
cat <<EOF

MeshBank is up. NodePorts: auth 30001, account 30002, transaction 30003,
payment 30004 (canary router), fraud 30005, notification 30006.

  make k8s-test        banking smoke test (every service hop over mTLS)
  make k8s-mtls-test   mesh security tests
  make k8s-canary-demo canary rollout demo
  make frontend        dashboard at http://localhost:5173 (choose Kubernetes)
EOF
