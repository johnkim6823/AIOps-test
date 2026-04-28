# Phase 6 — n8n 워크플로우 (탐지 → 진단 → 결정 → 승인 → 조치)

Alertmanager 가 `/webhook/oom` 으로 보낸 OOM 알람을 받아서 K8s API → LLM → RL → Slack → K8s patch 까지 자동으로 도는 워크플로우. 임포트 + 토큰 셋업까지 `import.sh` 가 책임진다.

## 노드 그래프

```
┌──────────────────────┐
│ Webhook /oom         │  Alertmanager → POST
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ Parse Alert (Code)   │  alertmanager payload → labels 추출
└──────────┬───────────┘
           │  (fan-out: 3 parallel HTTP)
   ┌───────┼────────┐
   ▼       ▼        ▼
┌─────┐ ┌─────┐ ┌─────────┐
│GET  │ │GET  │ │GET      │       모두 K8S_API_URL + K8S_TOKEN
│pod  │ │logs │ │events   │       으로 K8s API 직접 호출
└──┬──┘ └──┬──┘ └────┬────┘
   └───────┼─────────┘
           ▼
┌──────────────────────┐
│ Build LLM Prompt     │  describe/logs/events 합치고 system+user prompt 생성
│   (Code)             │
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ LLM Diagnose         │  Ollama /api/chat (format=json)
│ (HTTP → Ollama)      │
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ Parse LLM JSON       │  root_cause / confidence / evidence
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ RL Decide            │  rl-stub (또는 04-rl/serve.py) /decide
│ (HTTP)               │
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ Slack Post           │  chat.postMessage + Block Kit 버튼 (Approve / Reject)
└──────────────────────┘

────────────────────────  (별도 webhook 으로 비동기 콜백)  ────────────────────────

┌──────────────────────┐
│ Webhook /slack-approve│  Slack interactivity request URL
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ Parse Slack Action   │  payload= 디코드, action_id / decision JSON 추출
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ IF approve_patch ───┐
└──────────┬──────────┘
   true ▼     false ▼
┌─────────┐  ┌──────────────────┐
│ K8s     │  │ Slack Confirm    │
│ PATCH   │  │ Rejected         │
│ deploy  │  └──────────────────┘
└────┬────┘
     ▼
┌──────────────────────┐
│ Slack Confirm Applied│
└──────────────────────┘
```

## 사전 조건

- Phase 1 (kind 클러스터) → Phase 2 (관측성) → Phase 3 (n8n stack) 모두 완료
- Slack 설정 끝남 (`aiops-lab/docs/slack-setup.md`) — `SLACK_BOT_TOKEN`, `SLACK_WEBHOOK_URL`, `SLACK_CHANNEL` 가 `.env` 에 채워져 있어야 함

## 실행

```bash
chmod +x aiops-lab/06-n8n-flow/import.sh
./aiops-lab/06-n8n-flow/import.sh
```

자동 작업:
1. `rbac.yaml` 적용 (SA `aiops-n8n` + ClusterRole + Binding + Token Secret)
2. SA Token 추출 → `aiops-lab/03-workflow/.env` 의 `K8S_TOKEN`, `K8S_API_URL` 갱신
3. `n8n` / `n8n-worker` 컨테이너 재시작 (새 env 반영)
4. 워크플로우 JSON 임포트 안내 (UI 또는 REST)

## 워크플로우 JSON 임포트

`import.sh` 가 자동 임포트는 안 한다 (n8n API key 가 Phase 3 시점에 없음). UI 로:

1. 브라우저 → `http://localhost:5678` 또는 ngrok URL
2. **Workflows** → 우측 상단 **+** → **Import from File**
3. `aiops-lab/06-n8n-flow/n8n-workflow.json` 선택
4. 임포트 후 두 Webhook 노드의 **Production URL** 확인:
   - `Webhook /oom`           → Alertmanager 가 호출 (Phase 2 가 자동으로 보냄)
   - `Webhook /slack-approve` → Slack interactivity Request URL 에 등록
5. 우측 상단 **Active** 토글 ON

## Slack Interactivity URL 등록

n8n 의 Webhook 노드 Production URL 은 보통 `http://localhost:5678/webhook/slack-approve` 형태이지만, Slack 은 외부 https 만 허용한다. ngrok 사용:

```bash
ngrok http 5678
# Forwarding https://abcd-1234.ngrok-free.app -> http://localhost:5678
```

그 뒤 <https://api.slack.com/apps> → 해당 앱 → **Interactivity & Shortcuts** → Request URL = `https://abcd-1234.ngrok-free.app/webhook/slack-approve` 등록.

## 환경변수 요약

| 변수 | 출처 | 용도 |
|------|------|------|
| `K8S_API_URL`         | `import.sh` 자동 | n8n → kind API 서버 (`https://aiops-lab-control-plane:6443`) |
| `K8S_TOKEN`           | `import.sh` 자동 | SA `aiops-n8n` 의 long-lived bearer token |
| `OLLAMA_BASE_URL`     | Phase 3 compose | `http://ollama:11434` |
| `OLLAMA_MODEL`        | `.env`            | `qwen2.5:3b` 등 |
| `RL_DECIDE_URL`       | `.env`            | `http://rl-stub:8000/decide` 또는 학습 서버 |
| `SLACK_BOT_TOKEN`     | `.env`            | chat.postMessage 인증 |
| `SLACK_WEBHOOK_URL`   | `.env`            | 단순 문자열 알림 게시 (승인/거부 결과) |
| `SLACK_CHANNEL`       | `.env`            | 알림 채널 (예: `#alerts`) |

## 검증

```bash
# RBAC + 토큰
kubectl get sa,clusterrolebinding -A | grep aiops-n8n
kubectl -n kube-system get secret aiops-n8n-token -o jsonpath='{.data.token}' | base64 -d | head -c 60; echo

# n8n 안에서 K8s API 도달
docker compose -f aiops-lab/03-workflow/docker-compose.yaml exec n8n \
  sh -c 'wget -qO- --no-check-certificate \
    --header "Authorization: Bearer $K8S_TOKEN" \
    "$K8S_API_URL/api/v1/namespaces/prod/pods" | head -c 200; echo'

# Webhook 수동 호출 (가짜 알람 페이로드)
curl -s -X POST http://localhost:5678/webhook/oom \
  -H 'Content-Type: application/json' \
  -d '{"alerts":[{"labels":{"alertname":"PodOOMKilled","namespace":"prod","pod":"payment-api-xxx","container":"api","severity":"critical"},"annotations":{"summary":"manual test"}}]}'
```

## 산출물

| 파일 | 내용 |
|------|------|
| `rbac.yaml`                    | SA + ClusterRole + Binding + Token Secret |
| `prompts/system-prompt.md`     | LLM system 메시지 (JSON-only output 강제) |
| `prompts/user-prompt-template.md` | LLM user 메시지 템플릿 (자리표시자) |
| `n8n-workflow.json`            | 임포트용 워크플로우 (15 노드, 2 webhook) |
| `import.sh`                    | RBAC + 토큰 + .env 자동 셋업 |

## 알려진 제약

- 워크플로우 JSON 은 손으로 작성한 1.x 포맷. 임포트 후 일부 노드의 표현식 표시가 어그러질 수 있음 (UI 에서 한 번 클릭 → 저장하면 정규화됨).
- Slack 버튼 `value` 는 `JSON.stringify(JSON.stringify(...))` 로 두 번 인코딩됨. action.value 길이가 2000자 가까이 가면 끊길 수 있으니 메타데이터 최소화 권장.
- `Webhook /slack-approve` 는 `lastNode` 응답 모드라 Slack 의 3초 timeout 안에 응답해야 함. 진단/패치까지 다 끝내고 응답하면 timeout — 패치는 비동기로 흘러가게 두고 Webhook 응답은 즉시 200 으로 닫는 패턴 권장 (UI 에서 'Respond Immediately' 옵션 토글).
