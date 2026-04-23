# AIOps-test

한화시스템/ICT 신입사원 채용 사전과제 **"AIOps 기반 지능형 인프라 운영 설계"**
관련 산출물 저장소.

## 구성

```
AIOps-test/
├── mockup/      # 단일 파일 PoC 스크립트 (발표 데모용)
│   ├── aiops_poc.py
│   └── README.md
└── aiops-lab/   # 실제 end-to-end 테스트 환경 (kind + Prometheus + n8n + Ollama + Slack)
    ├── 00-preflight/check.sh
    └── docs/slack-setup.md
    (Phase 진행에 따라 01-k8s, 02-observability, ... 추가 예정)
```

## 어느 걸 먼저 봐야 하나

- **발표 데모만 보고 싶다**: `mockup/` 의 `python3 aiops_poc.py` 한 줄로 전체 흐름 재현 (외부 호출 전부 mock).
- **실제 클러스터에서 검증**: `aiops-lab/` 하위 Phase 0 부터 순차 실행. 현재 Phase 0(preflight) 완료.

## Phase 진행 상황

| Phase | 내용 | 상태 |
|-------|------|------|
| 0     | Preflight (의존성 점검)           | done |
| 1     | kind 클러스터 + ingress           | pending |
| 2     | kube-prometheus-stack + Loki      | pending |
| 3     | n8n + Redis + Qdrant + Ollama     | pending |
| 4     | RL 스텁 (FastAPI)                 | pending |
| 5     | Victim 앱 (OOMKilled 트리거)      | pending |
| 6     | n8n 워크플로우                    | pending |
| 7     | E2E 테스트                        | pending |

각 Phase 는 검증 후에만 다음으로 진행합니다.

## 병행 작업

Phase 0-2 진행 중에 [Slack 설정 가이드](aiops-lab/docs/slack-setup.md) 를 따라 워크스페이스·Webhook·ngrok 을 준비해두면 Phase 3 에서 바로 투입할 수 있습니다.
