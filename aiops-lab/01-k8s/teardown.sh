#!/usr/bin/env bash
# Phase 1 정리. 클러스터 통째로 날린다 (Phase 2~7 의 helm release / namespace 도 함께 사라짐).
set -euo pipefail
CLUSTER_NAME="aiops-lab"
if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  kind delete cluster --name "${CLUSTER_NAME}"
  echo "[teardown] kind cluster '${CLUSTER_NAME}' deleted"
else
  echo "[teardown] no cluster to delete"
fi
