"""
Phase 4a — RL 스텁 (휴리스틱).

n8n 워크플로우의 'RL 결정' 노드가 호출하는 단일 엔드포인트 /decide 를 노출한다.
실제 학습된 정책은 04-rl/serve.py 에 있고, 그쪽이 떠있으면 .env 의 RL_DECIDE_URL
을 그쪽으로 돌려서 같은 인터페이스로 교체할 수 있다 (무중단 A/B 가능).

휴리스틱 규칙:
    new_limit = max(current_usage * 2, current_limit * 1.5)
    hpa_max   = clamp(ceil(traffic_qps / 100), 2, 10)
    confidence = 0.65 (휴리스틱은 항상 중간값)
"""

import math
import os
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field


MIN_LIMIT_MB = 64
MAX_LIMIT_MB = 4096


class DecideRequest(BaseModel):
    service: str = Field(..., description="서비스 식별자 (로깅용)")
    current_limit_mb: float = Field(..., gt=0)
    current_usage_mb: float = Field(..., ge=0)
    usage_p50_mb: float | None = Field(None, ge=0)
    usage_p95_mb: float | None = Field(None, ge=0)
    usage_peak_mb: float | None = Field(None, ge=0)
    restart_count: int = Field(0, ge=0)
    traffic_qps: float = Field(0.0, ge=0)
    elapsed_steps: int = Field(0, ge=0)


class DecideResponse(BaseModel):
    recommended_limit_mb: int
    recommended_hpa_threshold: int = 70
    recommended_hpa_max: int = 5
    confidence: float
    reason: str
    policy: str = "heuristic"


app = FastAPI(title="AIOps RL Stub (heuristic)", version="0.1.0")


def _heuristic(req: DecideRequest) -> DecideResponse:
    # peak 가 있으면 그것을 기준으로, 없으면 현재 사용량
    peak = req.usage_peak_mb if req.usage_peak_mb is not None else req.current_usage_mb
    new_limit = max(int(peak * 2), int(req.current_limit_mb * 1.5))
    new_limit = max(MIN_LIMIT_MB, min(new_limit, MAX_LIMIT_MB))

    # 트래픽 기반 HPA max replicas 추정 (100 QPS 당 1 replica, 2~10 사이)
    if req.traffic_qps > 0:
        hpa_max = int(min(max(math.ceil(req.traffic_qps / 100.0), 2), 10))
    else:
        hpa_max = 5

    reason_parts = [f"peak={peak:.0f}Mi*2={int(peak * 2)}Mi"]
    if int(req.current_limit_mb * 1.5) > int(peak * 2):
        reason_parts.append(f"current*1.5={int(req.current_limit_mb * 1.5)}Mi -> floor")
    if req.restart_count >= 3:
        reason_parts.append(f"restart_count={req.restart_count} (high)")

    return DecideResponse(
        recommended_limit_mb=new_limit,
        recommended_hpa_threshold=70,
        recommended_hpa_max=hpa_max,
        confidence=0.65,
        reason="; ".join(reason_parts),
    )


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "policy": "heuristic", "version": "0.1.0"}


@app.post("/decide", response_model=DecideResponse)
def decide(req: DecideRequest) -> DecideResponse:
    return _heuristic(req)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )
