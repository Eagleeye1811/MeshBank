#!/usr/bin/env bash
# DEVELOPMENT ONLY: create a local RS256 key pair for JWT signing.
#
#   secrets/jwt/private/<kid>.pem   0600, read only by the auth service
#   secrets/jwt/public/<kid>.pem    0644, read by every service (and served as JWKS)
#
# The key ID must start with "dev-": production startup rejects such keys.
# Existing keys are never overwritten. To rotate, generate a new kid, point
# JWT_SIGNING_KEY_ID at it, and remove the old public key once tokens signed
# with it have expired (ACCESS_TOKEN_TTL_SECONDS). Key material is never printed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
KEYS_DIR="${JWT_KEYS_DIR:-$ROOT/secrets/jwt}"
KID="${DEV_JWT_KID:-dev-local-1}"

if [[ "${APP_ENV:-development}" == "production" ]]; then
  echo "Refusing to generate development keys when APP_ENV=production" >&2
  exit 2
fi
if [[ "$KID" != dev-* ]]; then
  echo "Development key IDs must start with 'dev-' (got '$KID')" >&2
  exit 2
fi
if [[ -f "$KEYS_DIR/private/$KID.pem" || -f "$KEYS_DIR/public/$KID.pem" ]]; then
  echo "Key '$KID' already exists in $KEYS_DIR; leaving it untouched."
  exit 0
fi

umask 077
"$PYTHON" -m common.keys --dir "$KEYS_DIR" --kid "$KID"
chmod 700 "$KEYS_DIR/private"
chmod 755 "$KEYS_DIR" "$KEYS_DIR/public"
echo "Use JWT_SIGNING_KEY_ID=$KID (the default for make run and docker compose)."
