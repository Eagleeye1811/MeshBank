#!/usr/bin/env bash
# Delete MeshBank kind clusters (all in-cluster data is lost).
#   k8s/delete.sh            the single cluster "meshbank"
#   k8s/delete.sh multi      the two clusters "meshbank-aws" and "meshbank-gcp"
set -euo pipefail
if [[ "${1:-single}" == multi ]]; then
  kind delete cluster --name meshbank-aws
  kind delete cluster --name meshbank-gcp
else
  kind delete cluster --name meshbank
fi
