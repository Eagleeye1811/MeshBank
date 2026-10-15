#!/usr/bin/env bash
# Deploy MeshBank across TWO local kind clusters that stand in for two clouds
# (macOS / Linux). Idempotent.
#
#   meshbank-aws: auth, account, transaction          (+ PostgreSQL, gateway)
#   meshbank-gcp: payment stable/canary, fraud, notification (+ PostgreSQL, gateway)
#
# Calls between the clusters leave through the caller's sidecar (mTLS with its
# own certificate), reach the other cluster's east-west gateway through a local
# alias Service, and are forwarded by SNI, still encrypted, to the target's
# sidecar, which verifies and authorizes the caller. The gateway holds no keys.
#
#   make k8s-multi-up               # build, create both clusters, deploy
#   SKIP_BUILD=1 make k8s-multi-up  # reuse existing images
#   ROTATE_CERTS=1 make k8s-multi-up
set -euo pipefail
# shellcheck source=k8s/lib.sh
source "$(dirname "$0")/lib.sh"

CLUSTERS="aws gcp"
GATEWAY_NODE_PORT=30443

cluster_images() {
  case "$1" in
    aws) echo "postgres:15-alpine nginx:alpine meshbank/auth-service:dev \
meshbank/account-service:dev meshbank/transaction-service:dev" ;;
    gcp) echo "postgres:15-alpine nginx:alpine meshbank/payment-service:dev \
meshbank/payment-service:v2 meshbank/fraud-service:dev meshbank/notification-service:dev" ;;
  esac
}

cluster_deployments() {
  case "$1" in
    aws) echo "auth-service account-service transaction-service east-west-gateway" ;;
    gcp) echo "payment-service payment-service-canary payment-router fraud-service \
notification-service east-west-gateway" ;;
  esac
}

other_cluster() { if [[ "$1" == aws ]]; then echo gcp; else echo aws; fi; }

node_ip() {
  docker inspect -f '{{(index .NetworkSettings.Networks "kind").IPAddress}}' \
    "meshbank-$1-control-plane"
}

preflight
require_no_cluster meshbank
build_images
for c in $CLUSTERS; do
  ensure_cluster "meshbank-$c" "k8s/multicluster/kind-$c.yaml"
  # shellcheck disable=SC2046  # word splitting intended
  load_images "meshbank-$c" $(cluster_images "$c")
done
mesh_material

WORKLOAD_FILES="k8s/mesh-certs.yaml k8s/multicluster/mesh-sidecar-configs.yaml \
k8s/multicluster/east-west-gateway.yaml k8s/mesh-router-config.yaml \
k8s/auth-service.yaml k8s/account-service.yaml k8s/transaction-service.yaml \
k8s/fraud-notification.yaml k8s/payment-service.yaml k8s/payment-canary.yaml k8s/nodeports.yaml"

for c in $CLUSTERS; do
  CTX="kind-meshbank-$c"
  other="$(other_cluster "$c")"
  # shellcheck disable=SC2046
  existing_deployments $(cluster_deployments "$c")
  apply_shared

  step "[$CTX] $c workloads, sidecars, east-west gateway"
  # Only this cluster's objects (and only its workloads' private keys).
  # shellcheck disable=SC2086
  (cd scripts && "$PYTHON" cluster_manifests.py "$c" $(printf '../%s ' $WORKLOAD_FILES)) \
    | k apply -f -

  # Point the alias Service at the other cluster's gateway NodePort. On real
  # clouds this address becomes the other cloud's load balancer.
  ip="$(node_ip "$other")"
  [[ -n "$ip" ]] || die "could not find the meshbank-$other node address"
  echo "  $other-gateway -> $ip:$GATEWAY_NODE_PORT"
  k apply -f - <<EOF
apiVersion: discovery.k8s.io/v1
kind: EndpointSlice
metadata:
  name: $other-gateway-remote
  namespace: $NS
  labels:
    kubernetes.io/service-name: $other-gateway
    endpointslice.kubernetes.io/managed-by: meshbank-deploy
addressType: IPv4
ports:
  - name: tls-passthru
    port: $GATEWAY_NODE_PORT
    protocol: TCP
endpoints:
  - addresses: ["$ip"]
EOF
  restart_existing
done

for c in $CLUSTERS; do
  CTX="kind-meshbank-$c"
  # shellcheck disable=SC2046
  wait_for $(cluster_deployments "$c")
done

for c in $CLUSTERS; do
  echo; echo "== kind-meshbank-$c"
  kubectl --context "kind-meshbank-$c" get pods -n "$NS"
done
cat <<EOF

MeshBank is up on two clusters.
  aws (kind-meshbank-aws): auth 30001, account 30002, transaction 30003, gateway 31443
  gcp (kind-meshbank-gcp): payment 30004, fraud 30005, notification 30006, gateway 32443

  make k8s-multi-test        banking smoke test (transfers and payments cross clusters)
  make k8s-multi-mtls-test   mesh security tests, including the gateways
  make k8s-multi-status
  MESHBANK_TOPOLOGY=multi make frontend
EOF
