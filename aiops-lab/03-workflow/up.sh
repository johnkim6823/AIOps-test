#!/usr/bin/env bash
# Phase 3 기동.
#   1) kind 클러스터 / 네트워크 사전 확인
#   2) kind internal kubeconfig export → ./n8n-kubeconfig
#   3) .env 가 없으면 .env.example 복사 + N8N_ENCRYPTION_KEY 자동 생성
#   4) docker compose up -d
#   5) 의존 서비스 ready 까지 대기 (redis → n8n → ollama → ollama-pull → rl-stub)
#   6) 헬스 요약 출력

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

CLUSTER_NAME="aiops-lab"
COMPOSE="docker compose"
PULL_TIMEOUT="${PULL_TIMEOUT:-1200}"   # 모델 다운로드는 첫 회 5~10분 걸릴 수 있음

log()   { printf '[03-workflow] %s\n' "$*"; }
warn()  { printf '[03-workflow] \033[33mWARN\033[0m %s\n' "$*"; }
err()   { printf '[03-workflow] \033[31mERR\033[0m  %s\n' "$*" >&2; }

trap 'err "abort (exit $?)"' ERR

# ---------- 1) kind 클러스터 / 네트워크 ----------
if ! command -v kind >/dev/null 2>&1; then
  err "'kind' not in PATH"; exit 1
fi
if ! kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  err "kind cluster '${CLUSTER_NAME}' not found. Run 01-k8s/bootstrap.sh first."
  exit 1
fi
if ! docker network inspect kind >/dev/null 2>&1; then
  err "docker network 'kind' not present (cluster may have been deleted)"
  exit 1
fi

# ---------- 2) kubeconfig (internal form) ----------
log "exporting internal kubeconfig for n8n container"
kind get kubeconfig --internal --name "${CLUSTER_NAME}" > "${SCRIPT_DIR}/n8n-kubeconfig"
chmod 600 "${SCRIPT_DIR}/n8n-kubeconfig"

# ---------- 3) .env ----------
if [ ! -f .env ]; then
  log ".env not found — generating from .env.example"
  cp .env.example .env
  # N8N_ENCRYPTION_KEY 를 매 환경마다 새로 생성 (default 값을 그대로 두지 않음)
  if command -v openssl >/dev/null 2>&1; then
    KEY="$(openssl rand -hex 24)"
    sed -i "s|^N8N_ENCRYPTION_KEY=.*|N8N_ENCRYPTION_KEY=${KEY}|" .env
    log "generated random N8N_ENCRYPTION_KEY (24 bytes hex)"
  else
    warn "openssl not found — N8N_ENCRYPTION_KEY left as default placeholder. EDIT .env BEFORE PRODUCTION."
  fi
  warn "EDIT aiops-lab/03-workflow/.env to set Slack creds before n8n workflow can post to Slack"
fi

# ---------- 4) compose up ----------
log "docker compose up -d"
${COMPOSE} up -d --remove-orphans

# ---------- 5) ready waits ----------
wait_healthy() {
  local svc="$1" timeout="${2:-120}"
  log "waiting for ${svc} to become healthy (timeout ${timeout}s)..."
  local elapsed=0
  while [ "${elapsed}" -lt "${timeout}" ]; do
    local state
    state="$(${COMPOSE} ps --format json "${svc}" 2>/dev/null \
      | jq -r 'select(.Service=="'"${svc}"'") | .Health' 2>/dev/null \
      || true)"
    case "${state}" in
      healthy) log "  ${svc}: healthy"; return 0 ;;
      unhealthy) err "  ${svc}: unhealthy"; ${COMPOSE} logs --tail=30 "${svc}"; return 1 ;;
    esac
    sleep 3; elapsed=$((elapsed + 3))
  done
  err "  ${svc}: ready timeout (${timeout}s)"
  ${COMPOSE} logs --tail=30 "${svc}" || true
  return 1
}

wait_healthy redis     30
wait_healthy qdrant    60
wait_healthy ollama    60
wait_healthy rl-stub   30
wait_healthy n8n       90
wait_healthy n8n-worker 60

# ---------- 6) ollama-pull 진행상태 (init container) ----------
log "ollama-pull progress (model: $(grep '^OLLAMA_MODEL=' .env | cut -d= -f2 || echo 'qwen2.5:3b'))"
PULL_ELAPSED=0
while true; do
  STATE="$(docker inspect aiops-ollama-pull --format '{{.State.Status}}' 2>/dev/null || echo absent)"
  case "${STATE}" in
    exited)
      EXIT_CODE="$(docker inspect aiops-ollama-pull --format '{{.State.ExitCode}}')"
      if [ "${EXIT_CODE}" = "0" ]; then
        log "  ollama-pull: completed (exit 0)"
        break
      else
        err "  ollama-pull: failed (exit ${EXIT_CODE})"
        ${COMPOSE} logs --tail=50 ollama-pull
        return 1
      fi
      ;;
    running) ;;
    absent)
      err "  ollama-pull container not found"
      return 1
      ;;
  esac
  if [ "${PULL_ELAPSED}" -ge "${PULL_TIMEOUT}" ]; then
    warn "  ollama-pull still running after ${PULL_TIMEOUT}s — continue (model 다운로드는 백그라운드)"
    break
  fi
  sleep 10
  PULL_ELAPSED=$((PULL_ELAPSED + 10))
  printf '   ollama-pull running... %ds/%ds\r' "${PULL_ELAPSED}" "${PULL_TIMEOUT}"
done
echo

# ---------- 7) status / next ----------
${COMPOSE} ps
log ""
log "phase 3 complete. UIs:"
log "  n8n        : http://localhost:5678"
log "  qdrant     : http://localhost:6333/dashboard"
log "  ollama     : curl http://localhost:11434/api/tags"
log "  rl-stub    : curl http://localhost:8000/health  (compose-internal only; not host-exposed)"
log ""
log "next: ./aiops-lab/05-victim-app/deploy.sh && ./aiops-lab/06-n8n-flow/import.sh"
