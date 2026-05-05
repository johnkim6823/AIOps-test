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
#   STEP 6  deployment limits.memory 가 변경되었는지 (patch 적용됨)
#   STEP 7  새 Pod 가 Running 상태 5분 유지하는지 (조치 효과 검증)
#
# 견고화 포인트:
#   - 모든 background 자식 (port-forward) 은 trap EXIT 에서 정리
#   - 실행 전체 로그를 ./e2e-<ts>.log 로 저장
#   - 단계별 timeout / 환경변수 override
#   - Ctrl-C 인터럽트 시 즉시 정리 후 종료

set -uo pipefail

CLUSTER_NAME="aiops-lab"
NS="prod"
DEPLOY="payment-api"
PROM_NS="monitoring"
N8N_URL="${N8N_URL:-http://localhost:5678}"

OOM_WAIT="${OOM_WAIT:-180}"
ALERT_WAIT="${ALERT_WAIT:-120}"
N8N_WAIT="${N8N_WAIT:-60}"
PATCH_WAIT="${PATCH_WAIT:-600}"
STABLE_WINDOW="${STABLE_WINDOW:-300}"

PASS="\033[32m[ OK ]\033[0m"
FAIL="\033[31m[FAIL]\033[0m"
WARN="\033[33m[WARN]\033[0m"
INFO="\033[36m[INFO]\033[0m"

# 실행 로그 보존 — n8n / kubectl / docker 출력 일부도 같이 남김
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${SCRIPT_DIR}/e2e-$(date +%Y%m%dT%H%M%S).log"
exec > >(tee -a "${LOG_FILE}") 2>&1
echo "[e2e] full log: ${LOG_FILE}"

# 모든 background pid 추적 → trap 에서 일괄 정리
declare -a BG_PIDS=()

cleanup() {
  local rc=$?
  echo
  if [ "${#BG_PIDS[@]}" -gt 0 ]; then
    echo "[e2e] cleaning up ${#BG_PIDS[@]} background process(es)"
    for pid in "${BG_PIDS[@]}"; do
      kill "${pid}" 2>/dev/null || true
    done
    # SIGTERM 전송 후 짧게 기다렸다가 SIGKILL 백업
    sleep 1
    for pid in "${BG_PIDS[@]}"; do
      kill -9 "${pid}" 2>/dev/null || true
    done
  fi
  echo "[e2e] exit=${rc}  log=${LOG_FILE}"
}
trap cleanup EXIT INT TERM

step() { printf '\n\033[1;36m=== STEP %s ===\033[0m\n' "$*"; }
ok()   { printf '%b %s\n' "${PASS}" "$*"; }
err()  { printf '%b %s\n' "${FAIL}" "$*"; }
warn() { printf '%b %s\n' "${WARN}" "$*"; }
info() { printf '%b %s\n' "${INFO}" "$*"; }

# port-forward 한 번 띄워서 background 로 두고 BG_PIDS 에 추가
start_port_forward() {
  local svc="$1" local_port="$2" remote_port="$3"
  kubectl -n "${PROM_NS}" port-forward "svc/${svc}" \
    "${local_port}:${remote_port}" >/dev/null 2>&1 &
  local pid=$!
  BG_PIDS+=("${pid}")
  # bind 대기
  for _ in $(seq 1 10); do
    if (echo > /dev/tcp/127.0.0.1/"${local_port}") >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

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
  err "kind cluster ${CLUSTER_NAME} not running"; exit 1
fi
ok "kind cluster present"

if ! kubectl -n "${PROM_NS}" get prometheusrules aiops-pod-oomkilled >/dev/null 2>&1; then
  err "PrometheusRule aiops-pod-oomkilled not installed (Phase 2 needed)"; exit 1
fi
ok "PrometheusRule installed"

if ! curl -fsS --max-time 3 "${N8N_URL}/healthz" >/dev/null 2>&1; then
  err "n8n not reachable at ${N8N_URL}"; exit 1
fi
ok "n8n healthz OK"

if ! kubectl -n "${NS}" get deploy "${DEPLOY}" >/dev/null 2>&1; then
  err "deployment ${NS}/${DEPLOY} not found (Phase 5 needed)"; exit 1
fi
ok "victim deployment present"

CURRENT_LIMIT="$(kubectl -n "${NS}" get deploy "${DEPLOY}" \
  -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}')"
info "current memory limit: ${CURRENT_LIMIT}"

# Alertmanager 단일 port-forward 를 미리 잡아두고 EXIT 에서 정리
if ! start_port_forward prom-stack-kube-prom-alertmanager 9093 9093; then
  warn "alertmanager port-forward failed — STEP 3 may report timeout"
fi

# ===================================================================
step 1 "trigger fresh OOM cycle (rollout restart)"

kubectl -n "${NS}" rollout restart deploy/"${DEPLOY}"
kubectl -n "${NS}" rollout status   deploy/"${DEPLOY}" --timeout=60s || true
NEW_POD="$(kubectl -n "${NS}" get pods -l app="${DEPLOY}" \
  --sort-by=.metadata.creationTimestamp \
  -o jsonpath='{.items[-1:].metadata.name}')"
info "new pod: ${NEW_POD}"

# ===================================================================
step 2 "wait for OOMKilled event"

oom_event_seen() {
  kubectl -n "${NS}" get events --sort-by=.lastTimestamp 2>/dev/null \
    | awk '/OOMKilled|OOMKill/ {found=1} END{exit !found}'
}
wait_until "${OOM_WAIT}" "OOMKilled event observed" 5 oom_event_seen || exit 1

# ===================================================================
step 3 "wait for Alertmanager firing"

alert_firing() {
  curl -fsS --max-time 5 \
    'http://127.0.0.1:9093/api/v2/alerts?filter=alertname%3DPodOOMKilled&active=true' 2>/dev/null \
    | grep -q '"alertname":"PodOOMKilled"'
}
wait_until "${ALERT_WAIT}" "Alertmanager firing PodOOMKilled" 5 alert_firing \
  || warn "Alertmanager firing 미확인 — 그래도 다음 단계 시도"

# ===================================================================
step 4 "wait for n8n workflow execution"

n8n_executed() {
  docker logs aiops-n8n-worker --tail=300 2>/dev/null \
    | grep -qE 'webhook/oom|Executing workflow|"Webhook /oom"'
}
wait_until "${N8N_WAIT}" "n8n workflow executed" 3 n8n_executed \
  || warn "n8n 실행 흔적 미확인 — Slack 채널에서 시각 확인 권장"

# ===================================================================
step 5 "USER ACTION: approve in Slack"

cat <<MSG

  ┌────────────────────────────────────────────────────────────────┐
  │ Slack 채널 (#alerts) 에서 'Approve' 버튼을 눌러주세요.         │
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
LAST_RESTARTS=0
while [ "$(date +%s)" -lt "${END}" ]; do
  RESTARTS="$(kubectl -n "${NS}" get pods -l app="${DEPLOY}" \
    -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null || echo 0)"
  PHASE="$(kubectl -n "${NS}" get pods -l app="${DEPLOY}" \
    -o jsonpath='{.items[0].status.phase}' 2>/dev/null || echo Unknown)"
  ELAPSED=$(( $(date +%s) - START ))
  printf '   stability check: phase=%s restarts=%s elapsed=%ds/%ds\r' \
    "${PHASE}" "${RESTARTS}" "${ELAPSED}" "${STABLE_WINDOW}"
  if [ "${PHASE}" != "Running" ] && [ "${PHASE}" != "Pending" ]; then
    echo
    err "pod left Running state after patch (phase=${PHASE})"
    kubectl -n "${NS}" describe pod -l app="${DEPLOY}" | tail -40
    exit 1
  fi
  if [ "${RESTARTS:-0}" != "${LAST_RESTARTS}" ] && [ "${RESTARTS:-0}" != "0" ]; then
    echo
    err "container restarted after patch (restarts=${RESTARTS}) — limit 부족 가능"
    kubectl -n "${NS}" describe pod -l app="${DEPLOY}" | tail -40
    exit 1
  fi
  LAST_RESTARTS="${RESTARTS:-0}"
  sleep 10
done
echo

ok "pod stable for ${STABLE_WINDOW}s with no restarts"
echo
printf '\033[1;32m=== E2E TEST PASSED ===\033[0m\n'
printf 'log: %s\n' "${LOG_FILE}"
