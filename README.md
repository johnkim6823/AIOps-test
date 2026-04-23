# AIOps PoC - `payment-api` OOMKilled 자동 진단·조치 데모

한화시스템/ICT 신입사원 채용 사전과제 **"AIOps 기반 지능형 인프라 운영 설계"**
발표용 최소 동작 PoC. 외부 API 호출 없이 단일 Python 스크립트로
**탐지 → 컨텍스트 수집 → LLM 진단 → RL 결정 → Slack 승인 → K8s patch**
의 전체 흐름을 시뮬레이션한다.

---

## 실행 방법

```bash
python3 aiops_poc.py
```

- Python 3.8+ 표준 라이브러리만 사용 (`pip install` 불필요)
- 외부 네트워크 호출 없음 (Prometheus/LLM/Slack/K8s 전부 mock)
- STEP 5에서 `y` 입력 시 조치 적용, `n` 입력 시 취소

---

## 시나리오

Kubernetes `prod` 네임스페이스의 `payment-api` Pod가 배포 직후
**OOMKilled(exit 137)로 반복 재시작**되는 상황.

운영자는 적정 `memory limit`을 모르는 상태. AIOps가

1. 알람을 수신하고,
2. `kubectl describe`/`logs`/`events`로 컨텍스트를 모은 뒤,
3. LLM으로 근본 원인을 추론하고,
4. RL로 새 `memory_limit`·HPA 임계값을 산출해,
5. Slack으로 운영자 승인을 받은 후,
6. K8s API로 `kubectl patch`를 적용

하는 전체 폐루프를 자동 수행한다.

---

## 아키텍처

> 본 PoC는 한화시스템/ICT 사전과제 발표를 위한 데모입니다. 실제 아키텍처는
> Prometheus/Loki/Alertmanager 관측성 계층, n8n 자동화 워크플로우(Queue Mode +
> Redis), vLLM+Llama-3.3-70B LLM 서빙, Ray RLlib+SAC+Reptile 메타러닝 기반 RL,
> Slack 승인, K8s API patch 단계로 구성되며, 본 스크립트는 이 흐름을
> 단일 Python 파일로 압축 시뮬레이션한 것입니다.

### 실제 아키텍처 흐름

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌────────────────┐
│ Prometheus   │──▶│ Alertmanager │──▶│ n8n Webhook  │──▶│ Context Collect│
│ Loki         │   │              │   │ (Queue Mode  │   │ kubectl / logs │
│              │   │              │   │  + Redis)    │   │ / events       │
└──────────────┘   └──────────────┘   └──────────────┘   └────────┬───────┘
                                                                  │
                                                                  ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌────────────────┐
│  K8s API     │◀──│ Slack Approve│◀──│ RL Decision  │◀──│ LLM Diagnosis  │
│  patch       │   │ (Block Kit)  │   │ Ray RLlib    │   │ vLLM +         │
│  deploy      │   │              │   │ SAC+Reptile  │   │ Llama-3.3-70B  │
└──────────────┘   └──────────────┘   └──────────────┘   └────────────────┘
```

### 스크립트 6단계 매핑

| STEP | 파일 내 함수 | 담당 컴포넌트 (실제) | 본 PoC에서의 대체 |
|------|-------------|---------------------|-------------------|
| 1    | `step1_detect()`         | Prometheus → Alertmanager → n8n Webhook | 하드코딩된 알람 dict |
| 2    | `step2_collect_context()`| n8n이 `kubectl` / K8s Python client 호출 | 하드코딩된 describe/logs/events 문자열 |
| 3a   | `step3a_llm_diagnose()`  | vLLM OpenAI 호환 API + Llama-3.3-70B | 고정 JSON 응답 반환 |
| 3b   | `decide_remediation()`   | Ray RLlib + SAC + Reptile policy network | rule-based (limit × 1.75, HPA 70% 고정) |
| 4    | `step4_notify_slack()`   | Slack Webhook + Block Kit | ASCII box 콘솔 출력 |
| 5    | `step5_execute()`        | K8s API `patch deployment` | `input()` 후 명령어 에코 |

---

## 실제 구현 시 교체 지점

| 위치 | Mock | 실제 구현 |
|------|------|-----------|
| `step1_detect` | 하드코딩된 alert payload | n8n Webhook 엔드포인트가 Alertmanager POST 수신 |
| `step2_collect_context` | 하드코딩된 describe/logs 문자열 | `subprocess.run(["kubectl","describe",...])` 또는 `kubernetes.client.CoreV1Api()` |
| `step3a_llm_diagnose` | `llm_response = {...}` | `requests.post("http://vllm-svc:8000/v1/chat/completions", json={...})` |
| `decide_remediation` | `new_limit = int(current * 1.75)` | 학습된 policy network `policy.compute_single_action(state)` |
| `step4_notify_slack` | `print(ASCII box)` | `requests.post(slack_webhook, json=block_kit_payload)` |
| `step5_execute` | `input("(y/n)")` | Slack Interactive Component 콜백 + K8s API `patch_namespaced_deployment` |

각 교체 지점은 `aiops_poc.py` 소스의 docstring·주석에
`# 실제 구현 시 여기에 ...` 형태로 표시되어 있다.

---

## 핵심 수치 (데모 결과)

| 항목 | 값 | 비고 |
|------|-----|------|
| 장애 유형              | OOMKilled (exit 137) | JVM heap 한계 도달 |
| LLM confidence         | 92%                  | 자동 조치 임계값 85% 상회 |
| memory_limit           | 512Mi → **896Mi**    | +75% (batch 처리 heap 여유 확보) |
| hpa_threshold          | **70%**              | scale-out 트리거 |
| safe_to_automate       | **True**             | confidence ≥85% & 증가율 ≤100% |
| 자동 처리 시간         | ≈ 수 초              | 수동 복구(25분) 대비 99.x% 단축 |

---

## 발표 시 체크포인트

1. 실행 직후: 컬러 STEP 헤더가 순차적으로 출력되며 pause가 자연스럽게 들어간다.
2. STEP 3a: **LLM 입력 프롬프트가 먼저 화면에 표시되고**, 그 아래 JSON 응답이 나온다 → "실제 API 호출 시 이 프롬프트가 그대로 들어갑니다" 설명 가능.
3. STEP 3b: `decide_remediation()` docstring에 Ray RLlib+SAC+Reptile이 명시되어 있어, 발표 중 코드를 띄워 "현재는 rule-based지만 교체 지점은 이 함수 한 곳"이라고 짚을 수 있다.
4. STEP 4: Slack Block Kit 대신 ASCII box로 동일 내용을 터미널에 표시 → 실물 Slack 없이 승인 화면을 재현.
5. STEP 5: 대화형 `y/n` 입력으로 승인 시나리오와 거부 시나리오를 둘 다 시연 가능.

---

## 파일 구조

```
.
├── aiops_poc.py   # 단일 실행 파일 (6단계 전체 흐름)
└── README.md      # 본 문서
```
