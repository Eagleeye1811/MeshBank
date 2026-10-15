#!/usr/bin/env bash
# DEVELOPMENT ONLY: delete local MeshBank data (SQLite files and simulated
# delivery logs) and, if Docker is available, the compose volumes.
# Requires deliberate confirmation: CONFIRM=yes scripts/reset_dev.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "${CONFIRM:-}" != "yes" ]]; then
  echo "This permanently deletes all local MeshBank development data:"
  ls -d "$ROOT"/services/*/data 2>/dev/null || echo "  (no local data directories)"
  echo "  + docker compose volumes for this project"
  echo "Re-run with CONFIRM=yes to proceed (e.g. 'make reset-dev CONFIRM=yes')."
  exit 1
fi
if [[ "${APP_ENV:-development}" == "production" ]]; then
  echo "Refusing to reset data when APP_ENV=production" >&2
  exit 2
fi

rm -rf "$ROOT"/services/*/data
echo "Removed local data directories."
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  (cd "$ROOT" && docker compose down --volumes --remove-orphans)
  echo "Removed docker compose containers and volumes."
fi
