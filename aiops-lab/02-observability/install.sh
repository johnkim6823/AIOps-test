#!/usr/bin/env bash
# Phase 2: kube-prometheus-stack + Loki/Promtail + OOM 알람 라우팅
#
# Idempotent: 같은 release 가 있으면 helm upgrade --install 로 갱신.
# n8n 이 안 떠있어도 설치는 끝까지 진행 (webhook 호출은 알람 발생 시점에만 일어남).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROM_NS=monitoring
PROM_RELEASE=prom-stack
LOKI_NS=logging
LOKI_RELEASE=loki

# Alertmanager 가 호출할 n8n webhook URL 자동 감지.
#
# Alertmanager Pod 는 kind 클러스터 안에 있고, n8n 은 호스트의 5678 포트로
# 떠있다. Pod → 호스트 도달 경로:
#   1순위: docker 의 'kind' 네트워크 gateway (보통 172.18.0.1) — 가장 안정적.
#          Pod traffic 이 cluster CNI → kind network gateway → host:5678 로 흐름.
#   2순위: VM 의 LAN IP (Bridged adapter 사용 시 외부에서도 도달).
#   3순위: 'host.docker.internal' — Linux Docker 24+ extra_hosts 설정 필요.
# 명시 지정: N8N_WEBHOOK_URL="http://10.0.2.2:5678/webhook/oom" ./install.sh
detect_host_ip() {
  local gw
  gw="$(docker network inspect kind --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null)"
  if [ -n "${gw}" ] && [ "${gw}" != "<no value>" ]; then
    echo "${gw}"; return
  fi
  ip route get 8.8.8.8 2>/dev/null \
    | awk '{for (i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}'
}
HOST_IP_DEFAULT="$(detect_host_ip)"
HOST_IP_DEFAULT="${HOST_IP_DEFAULT:-host.docker.internal}"
N8N_WEBHOOK_URL="${N8N_WEBHOOK_URL:-http://${HOST_IP_DEFAULT}:5678/webhook/oom}"

# 차트 버전 — 필요 시 갱신
PROM_CHART_VERSION="${PROM_CHART_VERSION:-62.6.0}"
LOKI_CHART_VERSION="${LOKI_CHART_VERSION:-2.10.2}"

log() { printf '[02-observability] %s\n' "$*"; }

# ------------------ helm repos ------------------
log "ensuring helm repos"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo add grafana              https://grafana.github.io/helm-charts             >/dev/null 2>&1 || true
helm repo update >/dev/null

# ------------------ namespaces ------------------
kubectl get ns "${PROM_NS}" >/dev/null 2>&1 || kubectl create ns "${PROM_NS}"
kubectl get ns "${LOKI_NS}" >/dev/null 2>&1 || kubectl create ns "${LOKI_NS}"

# ------------------ kube-prometheus-stack ------------------
log "installing/upgrading kube-prometheus-stack (release=${PROM_RELEASE}, chart=${PROM_CHART_VERSION})"
helm upgrade --install "${PROM_RELEASE}" prometheus-community/kube-prometheus-stack \
  --namespace "${PROM_NS}" \
  --version "${PROM_CHART_VERSION}" \
  --values "${SCRIPT_DIR}/values-prometheus.yaml" \
  --wait --timeout 10m

# ------------------ Loki + Promtail ------------------
log "installing/upgrading loki-stack (release=${LOKI_RELEASE}, chart=${LOKI_CHART_VERSION})"
helm upgrade --install "${LOKI_RELEASE}" grafana/loki-stack \
  --namespace "${LOKI_NS}" \
  --version "${LOKI_CHART_VERSION}" \
  --values "${SCRIPT_DIR}/values-loki.yaml" \
  --wait --timeout 10m

# ------------------ PrometheusRule (OOM 알람 + AIOps 스택 health) ------------------
log "applying PrometheusRule for PodOOMKilled"
kubectl apply -f "${SCRIPT_DIR}/oom-prometheus-rule.yaml"
log "applying PrometheusRule for AIOps stack self-monitoring"
kubectl apply -f "${SCRIPT_DIR}/aiops-stack-rule.yaml"

# ------------------ AlertmanagerConfig (n8n webhook) ------------------
log "rendering AlertmanagerConfig with N8N_WEBHOOK_URL=${N8N_WEBHOOK_URL}"
RENDERED="${SCRIPT_DIR}/alertmanager-config.rendered.yaml"
sed "s|__N8N_WEBHOOK_URL__|${N8N_WEBHOOK_URL}|g" \
  "${SCRIPT_DIR}/alertmanager-config.template.yaml" > "${RENDERED}"
kubectl apply -f "${RENDERED}"

# ------------------ verification ------------------
log "verification"
kubectl -n "${PROM_NS}" get pods
kubectl -n "${LOKI_NS}" get pods
kubectl -n "${PROM_NS}" get prometheusrules,alertmanagerconfigs

log "phase 2 complete."
log "  Prometheus  : kubectl -n ${PROM_NS} port-forward svc/${PROM_RELEASE}-kube-prom-prometheus 9090"
log "  Grafana     : kubectl -n ${PROM_NS} port-forward svc/${PROM_RELEASE}-grafana 3000:80   (admin / aiops-lab)"
log "  Alertmanager: kubectl -n ${PROM_NS} port-forward svc/${PROM_RELEASE}-kube-prom-alertmanager 9093"
log "next: ./aiops-lab/03-workflow/up.sh"
