#!/usr/bin/env bash
# Phase 1: kind 클러스터 생성 + ingress-nginx 설치.
#
# Idempotent: 이미 클러스터가 있으면 재사용. ingress 가 떠있으면 skip.
# 검증 단계 실패 시 즉시 exit 1.

set -euo pipefail

CLUSTER_NAME="aiops-lab"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIND_CONFIG="${SCRIPT_DIR}/kind-config.yaml"
INGRESS_MANIFEST="https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.2/deploy/static/provider/kind/deploy.yaml"

log() { printf '[bootstrap] %s\n' "$*"; }

# ----- 1) cluster -----
if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  log "kind cluster '${CLUSTER_NAME}' already exists, reusing"
else
  log "creating kind cluster '${CLUSTER_NAME}' from ${KIND_CONFIG}"
  kind create cluster --config "${KIND_CONFIG}" --wait 90s
fi

# kubectl context 정렬
kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null

# ----- 2) ingress-nginx -----
if kubectl -n ingress-nginx get deploy ingress-nginx-controller >/dev/null 2>&1; then
  log "ingress-nginx already installed, skipping"
else
  log "installing ingress-nginx (kind variant)"
  kubectl apply -f "${INGRESS_MANIFEST}"
fi

log "waiting for ingress-nginx admission webhook + controller to become Ready"
kubectl -n ingress-nginx wait --for=condition=available \
  deploy/ingress-nginx-controller --timeout=180s
kubectl -n ingress-nginx wait --for=condition=complete \
  job/ingress-nginx-admission-create --timeout=120s 2>/dev/null || true
kubectl -n ingress-nginx wait --for=condition=complete \
  job/ingress-nginx-admission-patch --timeout=120s 2>/dev/null || true

# ----- 3) verification -----
log "verifying nodes are Ready"
kubectl get nodes -o wide
ready_count=$(kubectl get nodes -o json | jq '[.items[].status.conditions[] | select(.type=="Ready" and .status=="True")] | length')
if [ "${ready_count}" -lt 3 ]; then
  log "FAIL: expected 3 Ready nodes, got ${ready_count}"
  exit 1
fi

log "verifying ingress-nginx pods"
kubectl -n ingress-nginx get pods

log "phase 1 complete: kind cluster '${CLUSTER_NAME}' + ingress-nginx ready"
log "next: ./aiops-lab/02-observability/install.sh"
