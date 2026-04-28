#!/usr/bin/env bash
# Phase 2 정리. helm release + namespace 삭제. (CRD 는 Phase 1 teardown 시 클러스터
# 삭제로 함께 사라지므로 여기서는 손대지 않는다.)
set -euo pipefail

PROM_NS=monitoring
LOKI_NS=logging

log() { printf '[02-observability/teardown] %s\n' "$*"; }

helm uninstall loki -n "${LOKI_NS}" 2>/dev/null || log "no loki release"
helm uninstall prom-stack -n "${PROM_NS}" 2>/dev/null || log "no prom-stack release"

kubectl delete ns "${LOKI_NS}" --ignore-not-found
kubectl delete ns "${PROM_NS}" --ignore-not-found

log "done"
