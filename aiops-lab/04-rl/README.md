# Phase 4b — RL 학습 (Reptile + SAC)

OOMKilled 자동 조치 정책을 합성 시뮬레이터 위에서 학습한다. K8s 나 n8n 과 독립적으로 동작하므로 Phase 1~3 와 병행 진행 가능.

---

## 핵심 설계

```
RightsizingEnv  (gymnasium)        ─┐
   ├ task profile 4종 (jvm-batch / py-leak / go-steady / node-burst)
   ├ state  : 8-dim (usage 분위수, restart 카운트, traffic, time cyclic, limit)
   ├ action : 1-dim continuous limit_factor ∈ [0.5, 3.0]
   └ reward : -cost*0.01 - oom*50 + stable*1                 │
                                                              │
                                  SAC (PyTorch, 자체 구현)    │
                                  ├ Gaussian actor + tanh squash
                                  ├ Twin Q + target soft update
                                  └ auto-α (entropy temperature)
                                                              │
                              Reptile meta-loop               │
                              θ ← θ + ε (mean(θ_i) - θ)       │
                              (각 task 마다 SAC inner loop K 스텝)
                                                              │
                                  체크포인트 sac_reptile.pt   │
                                                              │
                                  serve.py → FastAPI /decide ◀┘  ← n8n 호출
```

### 왜 Reptile 인가

새 서비스 (예: 처음 보는 `payment-api`) 의 메모리 패턴이 학습된 4개 task 와 정확히 일치하지 않더라도, 메타 학습된 weight 에서 출발하면 **few-shot adaptation** 만으로 빠르게 적정값을 찾을 수 있다. SAC 만 단일 task 로 학습하면 task 가 바뀔 때마다 처음부터 다시 학습해야 한다.

---

## 디렉터리

```
aiops-lab/04-rl/
├── env/
│   ├── __init__.py
│   └── rightsizing_env.py     # 합성 환경 + task 프로필
├── agent/
│   ├── __init__.py
│   ├── sac.py                 # SAC actor/critic/replay/auto-alpha
│   └── reptile.py             # 메타 outer loop
├── train.py                   # 학습 entrypoint
├── eval.py                    # 학습된 정책 회귀 테스트
├── serve.py                   # FastAPI /decide (n8n 이 호출)
├── requirements.txt
├── Makefile                   # make install / train / eval / serve
└── checkpoints/               # 학습 결과 (.gitignore)
```

---

## 빠른 실행 (VM 안 또는 어떤 Python 3.10+ 환경에서도)

```bash
cd aiops-lab/04-rl

# 1) venv + 의존성 설치 (~1.5 GB)
make install

# 2) 빠른 학습 (스모크 테스트, ~3분)
make train-quick

# 3) 정상 학습 (CPU ~30~45분)
make train

# 4) 평가
make eval

# 5) 추론 서버 띄우기 (n8n 연결용)
make serve   # http://0.0.0.0:8000
```

수동 옵션:

```bash
.venv/bin/python train.py --outer 50 --inner 2000 --tasks-per-iter 4 --meta-lr 0.3
.venv/bin/python eval.py  --ckpt checkpoints/sac_reptile.pt --episodes 10
.venv/bin/python serve.py --ckpt checkpoints/sac_reptile.pt --port 8000
```

---

## 기대 학습 결과 (`make eval` 마지막 출력)

이상적으로는 다음과 비슷한 수치가 나와야 한다 (CPU, seed=42, outer=30 기준):

```
task            reward     oom   final_mb    survival
------------------------------------------------------------
jvm-batch       ~  -20      ~ 0       ~ 700       100%
py-leak         ~   30      ~ 0       ~ 500       100%
go-steady       ~   45      ~ 0       ~ 256       100%
node-burst      ~   10      ~ 0       ~ 600       100%
------------------------------------------------------------
OVERALL         ~   15      ~ 0
```

OOM 횟수가 평균 0 에 가까워야 하고, `go-steady` 처럼 워크로드가 작은 task 는 limit 을 올리지 않는 (cost 절약) 패턴이 보여야 학습이 잘 된 것.

만약 모든 task 에서 limit 이 무한정 커지거나 (cost 패널티 무시) 계속 OOM 이 발생하면:
- `--meta-lr` 를 0.1 로 낮추기 (메타 업데이트가 너무 공격적)
- `--inner` 를 늘리기 (inner SAC 가 충분히 수렴 못 함)
- `agent/sac.py` 의 `target_entropy` 를 -2.0 정도로 낮추기

---

## n8n 연결 (Phase 6 에서 사용)

`serve.py` 가 띄운 8000 포트에 n8n 의 HTTP Request 노드가 다음을 보내면 된다:

```json
POST http://<vm-ip>:8000/decide
{
  "service": "payment-api",
  "current_limit_mb": 128,
  "current_usage_mb": 124,
  "usage_p95_mb": 126,
  "usage_peak_mb": 127,
  "restart_count": 3,
  "traffic_qps": 240,
  "elapsed_steps": 4
}
```

응답:

```json
{
  "recommended_limit_mb": 384,
  "recommended_hpa_threshold": 70,
  "confidence": 0.85,
  "reason": "sac+reptile policy: factor=2.32",
  "policy": "rl"
}
```

체크포인트 미존재 시 `policy: "heuristic"` 으로 fallback (mockup 과 동일한 1.5~2x 안전 휴리스틱).

---

## 알려진 제약

- 합성 환경이므로 실제 K8s metrics 분포와 차이 존재. Phase 6 통합 후 실제 Pod 사용량으로 fine-tune 하는 것이 이상적 (offline RL → online fine-tuning).
- OOM event 는 큰 음의 reward 라 actor 가 conservatively over-provision 으로 수렴하기 쉬움. cost 가중치 (`-0.01`) 를 키우면 과보호 완화 가능.
- VirtualBox CPU only: outer=30, inner=1500 기준 약 30~45분 소요. GPU 호스트에서 돌리면 5분 이내.

---

## 디버깅 팁

```bash
# Env 동작만 확인 (랜덤 정책)
.venv/bin/python -c "
from env import RightsizingEnv, list_tasks
import numpy as np
for t in list_tasks():
    env = RightsizingEnv(task_id=t, seed=0)
    obs, _ = env.reset()
    total = 0
    for _ in range(50):
        a = np.array([2.0], dtype=np.float32)  # 항상 2x 증량
        obs, r, term, trunc, info = env.step(a)
        total += r
        if term or trunc: break
    print(f'{t}: reward={total:.1f} oom={info[\"restart_count\"]}')
"
```
