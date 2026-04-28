#!/usr/bin/env bash
# Phase 3 기동.
#   1) kind 클러스터 존재 확인 (없으면 안내 후 종료)
#   2) kind 의 internal kubeconfig 를 ./n8n-kubeconfig 로 export (n8n 컨테이너 마운트용)
#   3) .env 가 없으면 .env.example 복사 (사용자가 실제 값을 채워야 함)
#   4) docker compose up -d
#   5) ollama-pull 진행 로그 표시 + 헬스 요약

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

CLUSTER_NAME="aiops-lab"

log() { printf '[03-workflow] %s\n' "$*"; }

# ---------- 1) kind 클러스터 / 네트워크 ----------
if ! kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  log "ERROR: kind cluster '${CLUSTER_NAME}' not found."
  log "       Run aiops-lab/01-k8s/bootstrap.sh first."
  exit 1
fi
if ! docker network inspect kind >/dev/null 2>&1; then
  log "ERROR: docker network 'kind' not present (cluster may have been deleted)."
  exit 1
fi

# ---------- 2) kubeconfig (internal form) ----------
log "exporting internal kubeconfig for n8n container"
kind get kubeconfig --internal --name "${CLUSTER_NAME}" > "${SCRIPT_DIR}/n8n-kubeconfig"
chmod 600 "${SCRIPT_DIR}/n8n-kubeconfig"

# ---------- 3) .env ----------
if [ ! -f .env ]; then
  log "no .env found — copying .env.example. EDIT IT before re-running for Slack integration."
  cp .env.example .env
fi

# ---------- 4) docker compose ----------
log "docker compose up -d"
docker compose up -d --remove-orphans

# ---------- 5) status / next steps ----------
log "waiting 5s for services to register..."
sleep 5
docker compose ps

log "ollama-pull progress (Ctrl-C to detach, container continues in background):"
log "  docker compose logs -f ollama-pull"
log ""
log "phase 3 complete. UIs (after a minute or two):"
log "  n8n        : http://localhost:5678"
log "  qdrant     : http://localhost:6333/dashboard"
log "  ollama API : curl http://localhost:11434/api/tags"
log ""
log "next: ./aiops-lab/05-victim-app/deploy.sh && ./aiops-lab/06-n8n-flow/import.sh"
