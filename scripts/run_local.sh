#!/usr/bin/env bash
# Run all six services locally (no Docker) with development settings.
# Each service uses services/<name>/.env if present, else built-in dev defaults.
# Data is stored in services/<name>/data/. Press Ctrl-C to stop everything.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
SERVICES=("auth:8001" "account:8002" "transaction:8003" "payment:8004" "fraud:8005" "notification:8006")
PIDS=()
# Development-only balance seeding for the account service (others ignore it).
export DEV_SEED_ENABLED="${DEV_SEED_ENABLED:-true}"

# RS256 keys from `make dev-keys`. Every service verifies with the public keys;
# only the auth service reads the private key (the others ignore these two).
export JWT_SIGNING_KEY_ID="${JWT_SIGNING_KEY_ID:-dev-local-1}"
export JWT_PUBLIC_KEYS_DIR="${JWT_PUBLIC_KEYS_DIR:-$ROOT/secrets/jwt/public}"
export JWT_PRIVATE_KEY_FILE="${JWT_PRIVATE_KEY_FILE:-$ROOT/secrets/jwt/private/$JWT_SIGNING_KEY_ID.pem}"
if [[ ! -f "$JWT_PRIVATE_KEY_FILE" || ! -d "$JWT_PUBLIC_KEYS_DIR" ]]; then
  echo "JWT keys not found ($JWT_PRIVATE_KEY_FILE). Run 'make dev-keys' first." >&2
  exit 1
fi

cleanup() {
  echo "Stopping services..."
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for entry in "${SERVICES[@]}"; do
  name="${entry%%:*}"; port="${entry##*:}"
  (
    cd "$ROOT/services/$name-service"
    exec "$PYTHON" -m uvicorn --factory app.main:create_app \
      --host 127.0.0.1 --port "$port" --no-access-log
  ) &
  PIDS+=($!)
  echo "started $name-service on http://127.0.0.1:$port (pid $!)"
done

wait
