# Phase 3 — n8n (Queue Mode) + Redis + Qdrant + Ollama + RL stub

도커 컴포즈로 자동화 + LLM + 벡터 DB + RL 추론 5종 세트를 한 번에 띄운다. n8n 은 main + worker 분리하여 Queue Mode (Redis 백엔드) 시연.

```
+-----------------------+      +--------+      +----------+
|  n8n (UI / REST)      |◀─────│ Redis  │      │ qdrant   |
|     :5678             │      │ (queue)│      │  :6333   │
└──────────┬────────────┘      └────────┘      └──────────┘
           │ enqueues jobs
           ▼
┌──────────────────────┐       ┌──────────┐    ┌──────────┐
│  n8n-worker          │──────▶│ ollama   │    │ rl-stub  │
│  (executes flows)    │       │  :11434  │    │  :8000   │
└──────────┬───────────┘       └──────────┘    └──────────┘
           │ kubectl via
           │ /home/node/.kube/config
           ▼
   kind cluster (Phase 1)
```

## 사전 조건

- Phase 1 의 kind 클러스터가 떠있어야 한다 (`kind get clusters` 에 `aiops-lab` 표시).
- `docker compose` 플러그인 사용 가능.
- (선택) Phase 4a 의 `04a-rl-stub/Dockerfile` 이 존재해야 `rl-stub` 빌드 성공. Phase 4a 작성 전이라면 `rl-stub` 서비스 라인을 임시 주석 처리해도 됨.

## 실행

```bash
chmod +x aiops-lab/03-workflow/up.sh aiops-lab/03-workflow/down.sh

# 1) .env 작성 (Slack 키 등)
cp aiops-lab/03-workflow/.env.example aiops-lab/03-workflow/.env
$EDITOR aiops-lab/03-workflow/.env

# 2) 기동
./aiops-lab/03-workflow/up.sh
```

`up.sh` 가 자동으로:
1. kind 클러스터 + `kind` docker 네트워크 존재 확인
2. `kind get kubeconfig --internal --name aiops-lab` 결과를 `./n8n-kubeconfig` 로 export
3. `.env` 없으면 example 복사
4. `docker compose up -d`
5. 상태 출력

## 핵심 환경변수 (`.env`)

| 변수 | 의미 | 예시 |
|------|------|------|
| `N8N_ENCRYPTION_KEY`     | n8n credential 암호화 키 (32자+, 변경 금지) | `please-change-me-32-chars-...` |
| `WEBHOOK_URL`            | 외부에서 n8n 에 도달할 base URL | ngrok 시 `https://abcd.ngrok-free.app/` |
| `OLLAMA_MODEL`           | 사용할 LLM 모델                | `qwen2.5:3b` (CPU) / `qwen2.5:7b` (GPU) |
| `SLACK_WEBHOOK_URL`      | 진단 결과 게시 채널 webhook    | `https://hooks.slack.com/services/...` |
| `SLACK_BOT_TOKEN`        | 봇 토큰 (승인 버튼 응답용)     | `xoxb-...` |
| `SLACK_SIGNING_SECRET`   | Slack interactivity 서명 검증 | `abcdef...` |
| `RL_DECIDE_URL`          | n8n → RL 추론 엔드포인트       | `http://rl-stub:8000/decide` |

## 검증

```bash
# n8n
curl -fsS http://localhost:5678/healthz   # {"status":"ok"}
docker compose logs --tail=20 n8n n8n-worker

# Qdrant
curl -fsS http://localhost:6333/collections   # {}

# Ollama (모델 다운로드 후)
curl -fsS http://localhost:11434/api/tags | jq
docker compose logs --tail=30 ollama-pull

# RL stub (Phase 4a 작성 후)
curl -fsS http://localhost:8000/health

# n8n 컨테이너에서 K8s API 도달 가능?
docker compose exec n8n kubectl get nodes
# → 3 node Ready 가 떠야 정상
```

## 산출물

| 파일 | 내용 |
|------|------|
| `docker-compose.yaml`   | 6 서비스 (redis / qdrant / ollama / ollama-pull / rl-stub / n8n + n8n-worker) |
| `.env.example`          | 채워야 할 환경변수 목록 |
| `.env`                  | (gitignore) 실제 값 — `up.sh` 첫 실행 시 자동 복사 |
| `n8n-kubeconfig`        | (gitignore) `kind get kubeconfig --internal` 결과 |
| `up.sh`, `down.sh`      | 단일 명령 기동/정리 |

## 정리

```bash
./aiops-lab/03-workflow/down.sh           # 컨테이너만 제거 (볼륨 유지)
./aiops-lab/03-workflow/down.sh -v        # 볼륨까지 wipe (n8n 워크플로우 / Ollama 모델 / Qdrant 데이터 모두 삭제)
```
