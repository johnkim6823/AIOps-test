#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS="prod"
echo "[05-victim-app/teardown] removing payment-api deployment + svc"
kubectl delete -f "${SCRIPT_DIR}/deployment.yaml" --ignore-not-found
echo "[05-victim-app/teardown] namespace '${NS}' is left intact (delete manually if desired)"
