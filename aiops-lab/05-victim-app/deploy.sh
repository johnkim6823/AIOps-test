#!/usr/bin/env bash
# Phase 5 배포: 이미지 빌드 → kind 노드에 로드 → namespace 생성 → deployment 적용.
#
# 'kind load docker-image' 가 핵심. kind 노드는 docker registry 가 아닌
# containerd 이고, host docker 의 이미지는 자동으로 보이지 않는다.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLUSTER_NAME="aiops-lab"
IMAGE="payment-api:demo"
NS="prod"

log() { printf '[05-victim-app] %s\n' "$*"; }

log "checking kind cluster '${CLUSTER_NAME}'"
if ! kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  log "ERROR: cluster not found. Run aiops-lab/01-k8s/bootstrap.sh first."
  exit 1
fi

log "building image ${IMAGE}"
docker build -t "${IMAGE}" "${SCRIPT_DIR}"

log "loading image into kind nodes (${CLUSTER_NAME})"
kind load docker-image "${IMAGE}" --name "${CLUSTER_NAME}"

log "ensuring namespace '${NS}' exists"
kubectl get ns "${NS}" >/dev/null 2>&1 || kubectl create ns "${NS}"

log "applying deployment"
kubectl apply -f "${SCRIPT_DIR}/deployment.yaml"

log "rollout status (will likely fail/restart due to OOM — that's expected)"
kubectl -n "${NS}" rollout status deploy/payment-api --timeout=60s || true

log "watch OOMKilled events (Ctrl-C to exit):"
log "  kubectl -n ${NS} get events --watch --field-selector involvedObject.name=payment-api"
log "or check pod state:"
log "  kubectl -n ${NS} get pods -w"
log ""
log "phase 5 complete. Phase 2 의 PrometheusRule 가 OOMKilled 를 감지하여"
log "Alertmanager → n8n webhook 트리거. (Phase 6 워크플로우가 떠있어야 다음 단계 진행)"
