# Phase 4a — RL 휴리스틱 스텁

학습 없이 즉시 사용 가능한 baseline. n8n 의 'RL 결정' 노드는 이 엔드포인트를 호출하면 되고, 인터페이스가 Phase 4b 의 학습된 모델 (`../04-rl/serve.py`) 과 동일하므로 `.env` 의 `RL_DECIDE_URL` 만 바꿔서 무중단 교체 가능.

## API

### `GET /health`
```json
{"status": "ok", "policy": "heuristic", "version": "0.1.0"}
```

### `POST /decide`
```json
// request
{
  "service": "payment-api",
  "current_limit_mb": 128,
  "current_usage_mb": 124,
  "usage_peak_mb": 127,
  "restart_count": 3,
  "traffic_qps": 240
}

// response
{
  "recommended_limit_mb": 254,
  "recommended_hpa_threshold": 70,
  "recommended_hpa_max": 3,
  "confidence": 0.65,
  "reason": "peak=127Mi*2=254Mi; restart_count=3 (high)",
  "policy": "heuristic"
}
```

## 휴리스틱 규칙

```
new_limit  = max(usage_peak * 2, current_limit * 1.5)            # MIN 64Mi, MAX 4096Mi
hpa_max    = clamp(ceil(traffic_qps / 100), 2, 10)
confidence = 0.65 (고정)
```

## 단독 실행 (디버깅)

Phase 3 의 `docker compose up` 이 자동으로 빌드/기동하지만, 따로 띄우려면:

```bash
cd aiops-lab/04a-rl-stub
docker build -t aiops-rl-stub .
docker run --rm -p 8000:8000 aiops-rl-stub
curl -s -X POST http://localhost:8000/decide \
  -H 'Content-Type: application/json' \
  -d '{"service":"x","current_limit_mb":128,"current_usage_mb":124,"usage_peak_mb":127,"restart_count":3,"traffic_qps":240}' | jq
```

## Phase 4b (학습된 정책) 로 전환

```bash
# 호스트에서 학습된 SAC 서버 띄우기
cd aiops-lab/04-rl
make install && make train && make serve PORT=8001 &

# n8n 환경변수만 변경
sed -i 's|RL_DECIDE_URL=.*|RL_DECIDE_URL=http://host.docker.internal:8001/decide|' aiops-lab/03-workflow/.env
docker compose -f aiops-lab/03-workflow/docker-compose.yaml restart n8n n8n-worker
```
