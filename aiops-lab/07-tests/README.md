# Phase 7 — End-to-End 검증

`payment-api` 의 OOMKilled 발생부터 자동 진단·승인·패치·안정화까지 한 번에 돌리는 단일 스크립트.

## 검증 항목

| Step | 검증 내용 | 통과 기준 |
|------|----------|----------|
| 0 | 사전 점검 (kind, prom-stack, n8n, victim) | 모두 reachable |
| 1 | victim rollout restart 로 OOM 사이클 reset | 새 Pod 가 시작됨 |
| 2 | OOMKilled 이벤트 발생 | `kubectl get events` 에 등장 (≤ 3분) |
| 3 | Alertmanager 가 `PodOOMKilled` firing | API v2 응답에 alert 1건 (≤ 2분) |
| 4 | n8n 워크플로우 실행 흔적 | n8n-worker 로그에 `webhook/oom` (≤ 1분) |
| 5 | **사용자가 Slack 에서 Approve** | (수동) |
| 6 | Deployment limits.memory 변경 | patch 적용 (≤ 10분 timeout) |
| 7 | 새 Pod 5분 무재시작 Running | restartCount=0 |

## 실행

```bash
chmod +x aiops-lab/07-tests/e2e-test.sh
./aiops-lab/07-tests/e2e-test.sh
```

Step 5 에서 화면에 안내가 뜨면 Slack 채널의 'Approve' 버튼을 누르세요. 그 뒤 자동으로 patch 적용 / 안정성 검증을 이어갑니다.

## 환경변수

| 변수 | 기본값 | 의미 |
|------|--------|------|
| `N8N_URL`        | `http://localhost:5678` | n8n healthz / executions API |
| `OOM_WAIT`       | 180     | OOMKilled 이벤트 대기 (초) |
| `ALERT_WAIT`     | 120     | Alertmanager firing 대기 |
| `N8N_WAIT`       | 60      | n8n 실행 흔적 대기 |
| `PATCH_WAIT`     | 600     | Slack 승인 + patch 적용 대기 (10분) |
| `STABLE_WINDOW`  | 300     | 새 Pod 안정 시간 (5분) |

## 실패 시나리오

| 증상 | 원인 후보 |
|------|----------|
| Step 2 timeout | `limits.memory` 가 너무 커서 OOM 안 남 / app.py 가 중단됨 |
| Step 3 timeout | PrometheusRule 미적용 / Alertmanager pod 미가동 |
| Step 4 timeout | n8n 워크플로우 inactive 상태 / Webhook URL 불일치 |
| Step 6 timeout | Slack approve 안 누름 / K8S_TOKEN 권한 부족 / patch 노드 오류 |
| Step 7 fail (restart) | 새 limit 도 부족 — RL 결정값 검증 필요 |

각각 디버깅:
- `kubectl -n prod describe pod <name>`
- `kubectl -n monitoring port-forward svc/prom-stack-kube-prom-alertmanager 9093` → http://localhost:9093
- `docker compose -f aiops-lab/03-workflow/docker-compose.yaml logs n8n n8n-worker`
- n8n UI → Executions → 마지막 실행 클릭 → 노드별 입출력 확인

## 빠른 재실행

전체 다시 돌리려면:

```bash
# 클러스터/스택은 그대로 두고 victim 만 reset
kubectl -n prod rollout restart deploy/payment-api
./aiops-lab/07-tests/e2e-test.sh
```

전부 wipe:

```bash
./aiops-lab/05-victim-app/teardown.sh
./aiops-lab/03-workflow/down.sh -v
./aiops-lab/02-observability/teardown.sh
./aiops-lab/01-k8s/teardown.sh
```
