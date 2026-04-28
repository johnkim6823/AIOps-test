#!/usr/bin/env bash
# Phase 7: end-to-end 검증.
#
# 진행 순서:
#   STEP 0  사전 점검 (kind / prom-stack / n8n / 워크플로우 활성 상태)
#   STEP 1  victim 재배포 (메모리 누수 사이클 reset)
#   STEP 2  OOMKilled 이벤트 발생 대기 (kubectl events)
#   STEP 3  Alertmanager 가 PodOOMKilled firing 으로 잡았는지
#   STEP 4  n8n 워크플로우가 1회 이상 실행되었는지 (executions API)
#   STEP 5  사용자 Slack 승인 안내 + 패치 폴링
#   STEP 6  deployment limits.memory 가 128Mi 보다 큰지 확인 (=patch 적용됨)
#   STEP 7  새 Pod 가 Running 상태 5분 유지하는지 (=조치 효과 검증)
#
# Slack 메시지 도착 자체는 시각으로 확인. 본 스크립트는 K8s/n8n 측면만 검증.

set -uo pipefail

CLUSTER_NAME="aiops-lab"
NS="prod"
DEPLOY="payment-api"
PROM_NS="monitoring"
N8N_URL="${N8N_URL:-http://localhost:5678}"

OOM_WAIT=180          # OOMKilled 이벤트 대기 (초)
ALERT_WAIT=120        # alert firing 대기
N8N_WAIT=60           # n8n 실행 등록 대기
PATCH_WAIT=600        # 사용자 승인 + 패치 적용 대기
STABLE_WINDOW=300     # 새 Pod 안정 시간 (5분)

PASS="\033[32m[ OK ]\033[0m"
FAIL="\033[31m[FAIL]\033[0m"
WARN="\033[33m[WARN]\033[0m"
INFO="\033[36m[INFO]\033[0m"

step() { printf '\n\033[1;36m=== STEP %s ===\033[0m\n' "$*"; }
ok()   { printf '%b %s\n' "${PASS}" "$*"; }
err()  { printf '%b %s\n' "${FAIL}" "$*"; }
warn() { printf '%b %s\n' "${WARN}" "$*"; }
info() { printf '%b %s\n' "${INFO}" "$*"; }

# 일정 시간 동안 condition 함수가 0 으로 끝날 때까지 폴링
wait_until() {
  local timeout="$1"; shift
  local desc="$1"; shift
  local interval="${1:-3}"; shift || true
  local elapsed=0
  while [ "${elapsed}" -lt "${timeout}" ]; do
    if "$@"; then
      ok "${desc} (after ${elapsed}s)"
      return 0
    fi
    sleep "${interval}"
    elapsed=$((elapsed + interval))
    printf '   waiting %s ... %ds/%ds\r' "${desc}" "${elapsed}" "${timeout}"
  done
  echo
  err "${desc} timed out after ${timeout}s"
  return 1
}

# ===================================================================
step 0 "preflight"

if ! kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  err "kind cluster ${CLUSTER_NAME} not running"
  exit 1
fi
ok "kind cluster present"

if ! kubectl -n "${PROM_NS}" get prometheusrules aiops-pod-oomkilled >/dev/null 2>&1; then
  err "PrometheusRule aiops-pod-oomkilled not installed (Phase 2 needed)"
  exit 1
fi
ok "PrometheusRule installed"

if ! curl -fsS "${N8N_URL}/healthz" >/dev/null 2>&1; then
  err "n8n not reachable at ${N8N_URL}"
  exit 1
fi
ok "n8n healthz OK"

if ! kubectl -n "${NS}" get deploy "${DEPLOY}" >/dev/null 2>&1; then
  err "deployment ${NS}/${DEPLOY} not found (Phase 5 needed)"
  exit 1
fi
ok "victim deployment present"

CURRENT_LIMIT="$(kubectl -n "${NS}" get deploy "${DEPLOY}" \
  -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}')"
info "current memory limit: ${CURRENT_LIMIT}"

# ===================================================================
step 1 "trigger fresh OOM cycle (rollout restart)"

kubectl -n "${NS}" rollout restart deploy/"${DEPLOY}"
kubectl -n "${NS}" rollout status   deploy/"${DEPLOY}" --timeout=60s || true
NEW_POD="$(kubectl -n "${NS}" get pods -l app="${DEPLOY}" \
  -o jsonpath='{.items[0].metadata.name}')"
info "new pod: ${NEW_POD}"

# ===================================================================
step 2 "wait for OOMKilled event"

oom_event_seen() {
  kubectl -n "${NS}" get events --sort-by=.lastTimestamp 2>/dev/null \
    | awk '/OOMKilled/ {print; found=1} END{exit !found}' >/dev/null
}
wait_until "${OOM_WAIT}" "OOMKilled event observed" 5 oom_event_seen || exit 1

# ===================================================================
step 3 "wait for Alertmanager firing alert"

alert_firing() {
  kubectl -n "${PROM_NS}" port-forward svc/prom-stack-kube-prom-alertmanager 9093 \
    >/dev/null 2>&1 &
  local pf_pid=$!
  sleep 2
  local resp
  resp="$(curl -fsS http://localhost:9093/api/v2/alerts?filter=alertname%3DPodOOMKilled\&active=true 2>/dev/null || true)"
  kill "${pf_pid}" 2>/dev/null || true
  echo "${resp}" | grep -q '"alertname":"PodOOMKilled"'
}
wait_until "${ALERT_WAIT}" "Alertmanager firing PodOOMKilled" 5 alert_firing || \
  warn "Alertmanager 미확인 — 그래도 다음 단계 시도"

# ===================================================================
step 4 "wait for n8n execution (workflow triggered)"

n8n_executed() {
  # 실행 이력은 인증 필요. 단순화 — n8n container 의 마지막 워크플로우
  # execution 로그에 'Webhook /oom' 흔적이 있는지 확인.
  docker logs aiops-n8n-worker --tail=200 2>/dev/null \
    | grep -q "Webhook /oom\|webhook/oom"
}
wait_until "${N8N_WAIT}" "n8n workflow executed" 3 n8n_executed || \
  warn "n8n 실행 자동 검증 실패 — Slack 채널에서 시각 확인 권장"

# ===================================================================
step 5 "USER ACTION: approve remediation in Slack"

cat <<MSG

  ┌────────────────────────────────────────────────────────────────┐
  │ Slack 채널 (#alerts) 에서 'Approve' 버튼을 눌러주세요.         │
  │ 그러면 n8n 이 K8s deployment 를 patch 합니다.                  │
  │                                                                │
  │ 본 스크립트는 deployment limits.memory 가 ${CURRENT_LIMIT} 보다 │
  │ 커질 때까지 polling 합니다 (timeout ${PATCH_WAIT}s).           │
  └────────────────────────────────────────────────────────────────┘

MSG

# ===================================================================
step 6 "wait for deployment patch"

patched() {
  local lim
  lim="$(kubectl -n "${NS}" get deploy "${DEPLOY}" \
    -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}')"
  [ -n "${lim}" ] && [ "${lim}" != "${CURRENT_LIMIT}" ]
}
wait_until "${PATCH_WAIT}" "deployment patched" 5 patched || exit 1

NEW_LIMIT="$(kubectl -n "${NS}" get deploy "${DEPLOY}" \
  -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}')"
ok "memory limit ${CURRENT_LIMIT} -> ${NEW_LIMIT}"

# ===================================================================
step 7 "verify new pod stable for ${STABLE_WINDOW}s"

kubectl -n "${NS}" rollout status deploy/"${DEPLOY}" --timeout=120s
START=$(date +%s)
END=$((START + STABLE_WINDOW))
while [ "$(date +%s)" -lt "${END}" ]; do
  RESTARTS="$(kubectl -n "${NS}" get pods -l app="${DEPLOY}" \
    -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null)"
  PHASE="$(kubectl -n "${NS}" get pods -l app="${DEPLOY}" \
    -o jsonpath='{.items[0].status.phase}' 2>/dev/null)"
  ELAPSED=$(( $(date +%s) - START ))
  printf '   stability check: phase=%s restarts=%s elapsed=%ds/%ds\r' \
    "${PHASE}" "${RESTARTS}" "${ELAPSED}" "${STABLE_WINDOW}"
  if [ "${PHASE}" != "Running" ]; then
    echo
    err "pod left Running state after patch (phase=${PHASE})"
    exit 1
  fi
  if [ "${RESTARTS:-0}" -gt 0 ]; then
    echo
    err "container restarted after patch (restarts=${RESTARTS}) — limit 부족 가능"
    exit 1
  fi
  sleep 10
done
echo

ok "pod stable for ${STABLE_WINDOW}s with no restarts"
echo
printf '\033[1;32m=== E2E TEST PASSED ===\033[0m\n'
