# shellcheck shell=bash
# Shared steps for k8s/deploy.sh (one cluster) and k8s/deploy-multicluster.sh
# (two clusters). Sourced, not run. Keep it bash 3.2 compatible (macOS).
#
# Every kubectl call goes through `k`, which pins the context in $CTX, so a
# deploy can never touch whatever cluster kubectl happens to point at.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
NS=meshbank
CTX=""

step() { printf '\n\033[36m== %s\033[0m\n' "$*"; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
k() { kubectl --context "$CTX" "$@"; }

preflight() {
  local tool
  for tool in docker kind kubectl; do
    command -v "$tool" >/dev/null || die "$tool not found (macOS: brew install $tool)"
  done
  docker info >/dev/null 2>&1 || die "Docker daemon not reachable; start Docker Desktop"
  [[ -x "$PYTHON" ]] || die "$PYTHON missing; run 'make install'"
  if [[ ! -f secrets/jwt/private/dev-local-1.pem || ! -f secrets/jwt/public/dev-local-1.pem ]]; then
    step "Generating development JWT keys"
    PYTHON="$PYTHON" DEV_JWT_KID=dev-local-1 scripts/dev_keys.sh
  fi
}

# Host ports 30001-30006 are mapped by whichever setup is running.
require_no_cluster() {
  local name
  for name in "$@"; do
    if kind get clusters 2>/dev/null | grep -qx "$name"; then
      die "kind cluster '$name' exists and uses the same host ports; delete it first \
(make k8s-down or make k8s-multi-down)"
    fi
  done
}

build_images() {
  local img
  if [[ "${SKIP_BUILD:-0}" != 1 ]]; then
    step "Building service images"
    docker compose build --quiet
    # The canary is a separate release artifact that reports version v2.
    docker build --quiet -f services/payment-service/Dockerfile \
      --build-arg SERVICE_VERSION=v2 -t meshbank/payment-service:v2 . >/dev/null
  fi
  for img in postgres:15-alpine nginx:alpine; do
    docker image inspect "$img" >/dev/null 2>&1 || docker pull --quiet "$img" >/dev/null
  done
}

# ensure_cluster NAME CONFIG
ensure_cluster() {
  step "kind cluster '$1'"
  if kind get clusters 2>/dev/null | grep -qx "$1"; then
    echo "  already exists"
  else
    kind create cluster --config "$2" --wait 120s
  fi
}

# load_images CLUSTER IMAGE...
load_images() {
  local cluster="$1" img
  shift
  step "Loading images into $cluster"
  for img in "$@"; do
    echo "  $img"
    if ! kind load docker-image "$img" --name "$cluster" >/dev/null 2>&1; then
      # Docker Desktop's containerd image store can make `kind load` fail on
      # multi-platform images; importing a saved tarball works.
      docker save "$img" | docker exec -i "$cluster-control-plane" \
        ctr --namespace=k8s.io images import - >/dev/null \
        || die "could not load $img into $cluster"
    fi
  done
}

mesh_material() {
  step "Mesh certificates and generated configs"
  if [[ "${ROTATE_CERTS:-0}" == 1 ]]; then
    (cd scripts && "$PYTHON" generate_mesh_certs.py --rotate)
  else
    (cd scripts && "$PYTHON" generate_mesh_certs.py)
  fi
  (cd scripts && "$PYTHON" generate_mesh_sidecars.py --check) \
    || die "generated mesh configs are stale: run 'make mesh-configs'"
}

# existing_deployments DEPLOYMENT... -> sets EXISTING to those present in $CTX.
# They are restarted after apply: images use fixed tags, and NGINX reads its
# config and certificates only at start.
existing_deployments() {
  local d
  EXISTING=""
  for d in "$@"; do
    k get deployment "$d" -n "$NS" >/dev/null 2>&1 && EXISTING="$EXISTING $d"
  done
  return 0
}

# Namespace, shared config and secrets, policies, PostgreSQL (in $CTX).
apply_shared() {
  step "[$CTX] namespace, config, secrets, policies"
  k apply -f k8s/namespace.yaml
  k apply -f k8s/configmap.yaml -f k8s/secrets.yaml -f k8s/network-policy-mesh.yaml
  k create secret generic jwt-public-keys -n "$NS" \
    --from-file=dev-local-1.pem=secrets/jwt/public/dev-local-1.pem \
    --dry-run=client -o yaml | k apply -f -
  k create secret generic jwt-signing-key -n "$NS" \
    --from-file=dev-local-1.pem=secrets/jwt/private/dev-local-1.pem \
    --dry-run=client -o yaml | k apply -f -
  # Objects from earlier versions of these manifests.
  k delete networkpolicy mesh-default-policy -n "$NS" --ignore-not-found
  k delete secret mesh-ca-cert -n "$NS" --ignore-not-found

  step "[$CTX] PostgreSQL"
  k apply -f k8s/postgres.yaml
  k rollout status deployment/postgres -n "$NS" --timeout=180s
}

# restart_existing: roll the deployments recorded by existing_deployments.
restart_existing() {
  local d
  for d in $EXISTING; do
    k rollout restart deployment/"$d" -n "$NS" >/dev/null
  done
}

# wait_for DEPLOYMENT...
wait_for() {
  local d
  step "[$CTX] waiting for rollouts"
  for d in "$@"; do
    k rollout status deployment/"$d" -n "$NS" --timeout=240s
  done
}
