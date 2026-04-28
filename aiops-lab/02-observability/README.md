# Phase 2 — 관측성 스택 + OOM 알람 라우팅

다음 4가지를 한 번에 띄운다:

1. **kube-prometheus-stack** — Prometheus + Alertmanager + Grafana + node-exporter + kube-state-metrics
2. **loki-stack** — Loki + Promtail (모든 Pod stdout/stderr 자동 수집)
3. **PrometheusRule `PodOOMKilled`** — `kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}==1` 30초 지속 시 알람
4. **AlertmanagerConfig** — `PodOOMKilled` 알람을 n8n Webhook (`http://<host>:5678/webhook/oom`) 로 라우팅

n8n 이 떠있지 않은 시점에도 설치는 정상 완료된다 — 실제 Webhook 호출은 알람 발생 시에만 일어난다.

## 실행

```bash
chmod +x aiops-lab/02-observability/install.sh aiops-lab/02-observability/teardown.sh
./aiops-lab/02-observability/install.sh
```

`install.sh` 는 idempotent — 다시 실행하면 helm upgrade 로 갱신만 한다.

### 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `N8N_WEBHOOK_URL` | `http://<auto-detected-host-ip>:5678/webhook/oom` | Alertmanager → n8n. NAT 환경 등 자동 감지 IP 가 안 맞으면 수동 지정. |
| `PROM_CHART_VERSION` | `62.6.0` | kube-prometheus-stack 차트 버전 |
| `LOKI_CHART_VERSION` | `2.10.2` | loki-stack 차트 버전 |

```bash
N8N_WEBHOOK_URL="http://10.0.2.2:5678/webhook/oom" ./install.sh
```

## 검증

```bash
# 1) PrometheusRule 등록 여부
kubectl -n monitoring get prometheusrules aiops-pod-oomkilled
kubectl -n monitoring get alertmanagerconfigs aiops-oom-to-n8n

# 2) Prometheus UI - rule 가 로드되었는지
kubectl -n monitoring port-forward svc/prom-stack-kube-prom-prometheus 9090
# http://localhost:9090 → Status → Rules → "aiops.pod.oomkilled" 그룹 노출

# 3) Alertmanager UI - route 가 반영되었는지
kubectl -n monitoring port-forward svc/prom-stack-kube-prom-alertmanager 9093
# http://localhost:9093 → Status → "aiops-oom-to-n8n" route 표시

# 4) Grafana — Loki/Prom 데이터 소스 모두 보이는지
kubectl -n monitoring port-forward svc/prom-stack-grafana 3000:80
# http://localhost:3000 (admin / aiops-lab)
```

## 산출물

| 파일 | 내용 |
|------|------|
| `values-prometheus.yaml`              | kube-prometheus-stack helm values (슬림 설정) |
| `values-loki.yaml`                    | loki-stack helm values |
| `oom-prometheus-rule.yaml`            | PrometheusRule CR |
| `alertmanager-config.template.yaml`   | AlertmanagerConfig CR (placeholder 포함) |
| `alertmanager-config.rendered.yaml`   | install.sh 가 자동 생성 — gitignore |
| `install.sh`, `teardown.sh`           | 한 줄 실행 |

## 정리

```bash
./aiops-lab/02-observability/teardown.sh
```

> Phase 1 의 `teardown.sh` 로 클러스터 자체를 지우면 monitoring/logging 네임스페이스와 helm release 도 함께 사라진다.
