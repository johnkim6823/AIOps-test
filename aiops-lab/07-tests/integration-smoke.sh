#!/usr/bin/env bash
# Integration smoke test — 실제 OOM 없이 n8n 워크플로우만 한 번 트리거.
#
# 호출 흐름:
#   1) 합성 Alertmanager payload 를 n8n /webhook/oom 으로 POST
#   2) n8n 워크플로우가 K8s API → Ollama → RL stub → Slack 까지 실행
#   3) Slack 채널에 진단 메시지가 떴으면 성공 (1분 내)
#
# 전제:
#   - Phase 3 의 n8n 컨테이너 떠있음
#   - Phase 6 의 워크플로우가 Active 상태
#   - 워크플로우의 K8s API 노드가 가리키는 Pod 가 클러스터에 존재 (없으면 GET 404)
#       → fixtures 의 pod 이름을 환경변수로 override 가능

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURE="${SCRIPT_DIR}/fixtures/alertmanager-oomkilled.json"

N8N_URL="${N8N_URL:-http://localhost:5678}"
WEBHOOK_PATH="${WEBHOOK_PATH:-/webhook/oom}"
TARGET_NAMESPACE="${TARGET_NAMESPACE:-prod}"
TARGET_POD="${TARGET_POD:-}"  # 실 Pod 이름. 비어있으면 자동 검출
TARGET_CONTAINER="${TARGET_CONTAINER:-api}"

PASS="\033[32m[ OK ]\033[0m"
FAIL="\033[31m[FAIL]\033[0m"
INFO="\033[36m[INFO]\033[0m"

ok()   { printf '%b %s\n' "${PASS}" "$*"; }
err()  { printf '%b %s\n' "${FAIL}" "$*" >&2; }
info() { printf '%b %s\n' "${INFO}" "$*"; }

echo "=== Integration Smoke Test ==="
echo

# ---- preflight ----
if ! curl -fsS --max-time 3 "${N8N_URL}/healthz" >/dev/null; then
  err "n8n healthz unreachable at ${N8N_URL}"; exit 1
fi
ok "n8n healthz"

# 실 pod 이름 자동 검출 (있으면)
if [ -z "${TARGET_POD}" ]; then
  if command -v kubectl >/dev/null 2>&1; then
    TARGET_POD="$(kubectl -n "${TARGET_NAMESPACE}" get pods -l app=payment-api \
      -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  fi
fi
if [ -z "${TARGET_POD}" ]; then
  TARGET_POD="payment-api-7d4f8b9c6-xk2mp"   # fixture 기본값
  info "real Pod not found — using fixture name '${TARGET_POD}' (K8s GET will 404, workflow handles it)"
else
  info "using real Pod: ${TARGET_POD}"
fi

# fixture 에서 namespace/pod 치환한 payload 생성
PAYLOAD="$(jq \
  --arg ns "${TARGET_NAMESPACE}" \
  --arg pod "${TARGET_POD}" \
  --arg ct "${TARGET_CONTAINER}" \
  '
  .alerts[0].labels.namespace = $ns
  | .alerts[0].labels.pod = $pod
  | .alerts[0].labels.container = $ct
  | .groupLabels.namespace = $ns
  | .groupLabels.pod = $pod
  ' "${FIXTURE}")"

# ---- 1) trigger ----
info "POST ${N8N_URL}${WEBHOOK_PATH}"
HTTP_CODE="$(curl -s -o /tmp/n8n-resp.json -w '%{http_code}' \
  -X POST "${N8N_URL}${WEBHOOK_PATH}" \
  -H 'Content-Type: application/json' \
  -d "${PAYLOAD}")"

if [ "${HTTP_CODE}" -ge 200 ] && [ "${HTTP_CODE}" -lt 300 ]; then
  ok "webhook accepted (HTTP ${HTTP_CODE})"
elif [ "${HTTP_CODE}" = "404" ]; then
  err "webhook returned 404 — 워크플로우 Active 상태 확인 또는 path 확인"
  cat /tmp/n8n-resp.json 2>/dev/null
  exit 1
else
  err "webhook returned HTTP ${HTTP_CODE}"
  cat /tmp/n8n-resp.json 2>/dev/null
  exit 1
fi

# ---- 2) execution evidence ----
info "checking n8n-worker logs for execution trace (10s)"
sleep 10

if docker logs aiops-n8n-worker --tail=200 2>/dev/null \
    | grep -qE "Webhook /oom|Executing workflow"; then
  ok "n8n executed the workflow"
else
  err "no execution trace in n8n-worker logs"
  echo "--- n8n-worker last 30 lines ---"
  docker logs aiops-n8n-worker --tail=30 2>/dev/null || true
  exit 1
fi

# Code 노드 에러 검색
if docker logs aiops-n8n-worker --tail=500 2>/dev/null \
    | grep -E "Error in node|ERROR.*workflow"; then
  err "n8n reported node error(s) above"
  exit 1
fi
ok "no node errors in recent worker logs"

echo
printf '\033[1;32m=== SMOKE TEST PASSED ===\033[0m\n'
echo "다음: Slack 채널 (#alerts) 에서 진단 메시지가 도착했는지 시각 확인."
