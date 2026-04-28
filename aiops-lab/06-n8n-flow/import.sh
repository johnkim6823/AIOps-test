#!/usr/bin/env bash
# Phase 6 셋업.
#   1) RBAC (ServiceAccount + ClusterRole + Binding + Token Secret) 적용
#   2) SA Token 추출 → 03-workflow/.env 에 K8S_TOKEN, K8S_API_URL 주입
#   3) n8n / n8n-worker 컨테이너 재시작 (env 반영)
#   4) 워크플로우 JSON import 안내 (n8n REST API 또는 UI)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/03-workflow/.env"
WORKFLOW_JSON="${SCRIPT_DIR}/n8n-workflow.json"
CLUSTER_NAME="aiops-lab"

log() { printf '[06-n8n-flow] %s\n' "$*"; }

# ---------- preconditions ----------
if ! kubectl get nodes >/dev/null 2>&1; then
  log "ERROR: kubectl context is not set. Run aiops-lab/01-k8s/bootstrap.sh first."
  exit 1
fi
if [ ! -f "${ENV_FILE}" ]; then
  log "ERROR: ${ENV_FILE} not found. Run aiops-lab/03-workflow/up.sh first."
  exit 1
fi

# ---------- 1) RBAC ----------
log "applying RBAC (SA / ClusterRole / Binding / Token Secret)"
kubectl apply -f "${SCRIPT_DIR}/rbac.yaml"

# Token Secret 이 채워질 때까지 대기 (controller 가 token data 를 비동기로 기록)
log "waiting for SA token secret to be populated"
for i in {1..30}; do
  TOKEN_B64="$(kubectl -n kube-system get secret aiops-n8n-token -o jsonpath='{.data.token}' 2>/dev/null || true)"
  if [ -n "${TOKEN_B64}" ]; then
    break
  fi
  sleep 1
done
if [ -z "${TOKEN_B64:-}" ]; then
  log "ERROR: SA token secret stayed empty for 30s"
  exit 1
fi
TOKEN="$(printf '%s' "${TOKEN_B64}" | base64 -d)"

# ---------- 2) K8s API URL: 도커 'kind' 네트워크에서 보이는 호스트명 ----------
# Phase 3 의 n8n 컨테이너가 attach 된 'kind' 네트워크 안에서는 control-plane
# 컨테이너가 'aiops-lab-control-plane' DNS 로 해석된다.
K8S_API_URL="${K8S_API_URL:-https://${CLUSTER_NAME}-control-plane:6443}"

# ---------- 3) .env 업데이트 ----------
update_env() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" "${ENV_FILE}"; then
    # in-place 치환. value 의 슬래시 / 앰퍼샌드를 안전 처리
    local esc; esc="$(printf '%s' "${value}" | sed -e 's|[\/&]|\\&|g')"
    sed -i "s|^${key}=.*|${key}=${esc}|" "${ENV_FILE}"
  else
    printf '%s=%s\n' "${key}" "${value}" >> "${ENV_FILE}"
  fi
}

log "writing K8S_API_URL and K8S_TOKEN to ${ENV_FILE}"
update_env K8S_API_URL "${K8S_API_URL}"
update_env K8S_TOKEN   "${TOKEN}"

# ---------- 4) n8n 재시작 (env 반영) ----------
if docker compose -f "${ROOT_DIR}/03-workflow/docker-compose.yaml" ps --status running --quiet n8n >/dev/null 2>&1; then
  log "restarting n8n + n8n-worker to pick up new env"
  docker compose -f "${ROOT_DIR}/03-workflow/docker-compose.yaml" up -d n8n n8n-worker
fi

# ---------- 5) workflow import 안내 ----------
log "RBAC + .env 셋업 완료."
log ""
log "워크플로우 import 옵션:"
log "  A) n8n UI 사용 (가장 안정적):"
log "     1. http://localhost:5678 접속 (또는 ngrok URL)"
log "     2. Workflows → Import from File → ${WORKFLOW_JSON}"
log "     3. 임포트 후 'Webhook /oom' 노드 production URL 확인 → Phase 2 의"
log "        AlertmanagerConfig URL 과 일치하는지 검증 (.../webhook/oom)"
log "     4. 우측 상단 'Active' 토글 켜기"
log ""
log "  B) n8n REST API (실험적, n8n Basic Auth 또는 API key 필요):"
log "     curl -X POST -H 'X-N8N-API-KEY: <key>' \\"
log "          -H 'Content-Type: application/json' \\"
log "          -d @${WORKFLOW_JSON} \\"
log "          http://localhost:5678/api/v1/workflows"
log ""
log "다음:"
log "  - Slack interactivity Request URL = <ngrok URL>/webhook/slack-approve"
log "  - Phase 5 victim 배포 → Alertmanager 가 ${K8S_API_URL/control-plane/host:5678}/webhook/oom 호출 → 워크플로우 트리거"
log "  - 또는 ./aiops-lab/07-tests/e2e-test.sh 로 자동 검증"
