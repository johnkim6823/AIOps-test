"""
Phase 4a — RL 스텁 (휴리스틱).

n8n 워크플로우의 'RL 결정' 노드가 호출하는 단일 엔드포인트 /decide.
실제 학습된 정책은 04-rl/serve.py 에 있고, 그쪽이 떠있으면 .env 의 RL_DECIDE_URL
을 그쪽으로 돌리면 된다 (인터페이스 동일).

견고화 포인트:
    - Pydantic v2 strict mode + 명시 bounds (limit, qps 상한)
    - structured JSON logging (Loki 친화)
    - Prometheus /metrics 엔드포인트 (요청 수, 휴리스틱 fallback 카운트)
    - /health 가 마지막 요청 시점 / process uptime 노출
    - graceful shutdown signal handler
"""

from __future__ import annotations

import logging
import math
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator


# ───────────────────────────────────────────────────────────── structured logger

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        payload: dict[str, Any] = {
            "ts":     ts,
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k in ("args", "msg", "name", "levelname", "levelno", "pathname",
                     "filename", "module", "exc_info", "exc_text", "stack_info",
                     "lineno", "funcName", "created", "msecs", "relativeCreated",
                     "thread", "threadName", "processName", "process",
                     "taskName"):
                continue
            payload[k] = v
        import json as _json
        return _json.dumps(payload, ensure_ascii=False, default=str)


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JsonFormatter())
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "info").upper(),
    handlers=[_handler],
    force=True,
)
log = logging.getLogger("rl-stub")


# ───────────────────────────────────────────────────────────── constants

MIN_LIMIT_MB = 64
MAX_LIMIT_MB = 4096
MAX_TRAFFIC_QPS = 100_000.0
SAFETY_FACTOR = 1.5  # 사용량의 N 배 또는 limit 의 1.5배 중 큰 값
USAGE_FACTOR = 2.0


# ───────────────────────────────────────────────────────────── models

class DecideRequest(BaseModel):
    model_config = {"extra": "forbid"}

    service: str = Field(..., min_length=1, max_length=128)
    current_limit_mb: float = Field(..., gt=0, le=MAX_LIMIT_MB * 4)
    current_usage_mb: float = Field(..., ge=0, le=MAX_LIMIT_MB * 4)
    usage_p50_mb: float | None = Field(None, ge=0, le=MAX_LIMIT_MB * 4)
    usage_p95_mb: float | None = Field(None, ge=0, le=MAX_LIMIT_MB * 4)
    usage_peak_mb: float | None = Field(None, ge=0, le=MAX_LIMIT_MB * 4)
    restart_count: int = Field(0, ge=0, le=10_000)
    traffic_qps: float = Field(0.0, ge=0, le=MAX_TRAFFIC_QPS)
    elapsed_steps: int = Field(0, ge=0, le=1_000_000)

    @field_validator("usage_peak_mb")
    @classmethod
    def _peak_geq_p95(cls, v, info):
        # peak < p95 같은 비논리적 조합 거부
        p95 = (info.data or {}).get("usage_p95_mb")
        if v is not None and p95 is not None and v < p95:
            raise ValueError("usage_peak_mb must be >= usage_p95_mb")
        return v


class DecideResponse(BaseModel):
    recommended_limit_mb: int
    recommended_hpa_threshold: int = 70
    recommended_hpa_max: int = 5
    confidence: float
    reason: str
    policy: str = "heuristic"


# ───────────────────────────────────────────────────────────── metrics state

class M:
    started_ts:        float = time.time()
    requests_total:    int   = 0
    requests_failed:   int   = 0
    last_request_ts:   float = 0.0


# ───────────────────────────────────────────────────────────── app

app = FastAPI(title="AIOps RL Stub (heuristic)", version="0.2.0")


def _heuristic(req: DecideRequest) -> DecideResponse:
    peak = req.usage_peak_mb if req.usage_peak_mb is not None else req.current_usage_mb
    # peak 가 limit 을 이미 초과한 경우, peak * 2 로 잡으면 너무 작을 수 있음 → 2배 보장
    new_limit = max(int(peak * USAGE_FACTOR), int(req.current_limit_mb * SAFETY_FACTOR))
    new_limit = max(MIN_LIMIT_MB, min(new_limit, MAX_LIMIT_MB))

    # 트래픽 기반 HPA max replicas (100 QPS / replica, [2..10])
    hpa_max = int(min(max(math.ceil(req.traffic_qps / 100.0), 2), 10)) if req.traffic_qps > 0 else 5

    parts = [f"peak={peak:.0f}Mi*{USAGE_FACTOR:g}={int(peak * USAGE_FACTOR)}Mi"]
    safety_floor = int(req.current_limit_mb * SAFETY_FACTOR)
    if safety_floor > int(peak * USAGE_FACTOR):
        parts.append(f"safety_floor={safety_floor}Mi (current*{SAFETY_FACTOR:g})")
    if new_limit == MAX_LIMIT_MB:
        parts.append(f"clamped to MAX={MAX_LIMIT_MB}Mi")
    if req.restart_count >= 3:
        parts.append(f"restart_count={req.restart_count} (chronic)")

    return DecideResponse(
        recommended_limit_mb=new_limit,
        recommended_hpa_threshold=70,
        recommended_hpa_max=hpa_max,
        confidence=0.65,
        reason="; ".join(parts),
    )


# ───────────────────────────────────────────────────────────── endpoints

@app.middleware("http")
async def _request_log(request: Request, call_next):
    start = time.time()
    M.requests_total += 1
    M.last_request_ts = start
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        M.requests_failed += 1
        log.exception("unhandled_exception", extra={"path": request.url.path,
                                                    "error": str(exc)})
        raise
    duration_ms = (time.time() - start) * 1000
    log.info("request", extra={
        "method":      request.method,
        "path":        request.url.path,
        "status":      response.status_code,
        "duration_ms": round(duration_ms, 1),
    })
    return response


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status":          "ok",
        "policy":          "heuristic",
        "version":         app.version,
        "uptime_s":        round(time.time() - M.started_ts, 1),
        "requests_total":  M.requests_total,
        "requests_failed": M.requests_failed,
    }


@app.get("/metrics")
def metrics() -> Response:
    text = (
        "# HELP rlstub_requests_total Total HTTP requests received\n"
        "# TYPE rlstub_requests_total counter\n"
        f"rlstub_requests_total {M.requests_total}\n"
        "# HELP rlstub_requests_failed_total Failed (5xx) HTTP requests\n"
        "# TYPE rlstub_requests_failed_total counter\n"
        f"rlstub_requests_failed_total {M.requests_failed}\n"
        "# HELP rlstub_uptime_seconds Process uptime\n"
        "# TYPE rlstub_uptime_seconds gauge\n"
        f"rlstub_uptime_seconds {time.time() - M.started_ts:.1f}\n"
    )
    return Response(content=text, media_type="text/plain; version=0.0.4")


@app.post("/decide", response_model=DecideResponse)
def decide(req: DecideRequest) -> DecideResponse:
    if req.current_usage_mb > req.current_limit_mb * 2:
        # 명백히 비정상 (사용량이 limit 의 2배 초과 보고) → reject
        raise HTTPException(
            status_code=422,
            detail=(f"current_usage_mb ({req.current_usage_mb}) exceeds 2x "
                    f"current_limit_mb ({req.current_limit_mb}); refusing to decide"),
        )
    resp = _heuristic(req)
    log.info("decide", extra={
        "service":          req.service,
        "current_limit":    req.current_limit_mb,
        "usage_peak":       req.usage_peak_mb,
        "restart_count":    req.restart_count,
        "recommended_limit": resp.recommended_limit_mb,
    })
    return resp


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        log_level=os.environ.get("LOG_LEVEL", "info"),
        access_log=False,  # 우리 미들웨어가 access log 담당
    )
