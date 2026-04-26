"""
Phase 4 추론 서비스 (FastAPI). n8n 이 호출하는 /decide 엔드포인트를 노출한다.

학습된 SAC 체크포인트가 있으면 그것을 로드해서 정책으로 사용하고,
없으면 휴리스틱 폴백 (current_usage * 2) 으로 동작한다.

Run:
    python serve.py                                   # 0.0.0.0:8000
    python serve.py --ckpt checkpoints/sac_reptile.pt # 명시
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import torch
from fastapi import FastAPI
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent))

from env.rightsizing_env import (  # noqa: E402
    BASELINE_MAX_MB, BASELINE_MIN_MB, EPISODE_T, OBS_DIM, ACT_LOW, ACT_HIGH,
)
from agent.sac import SAC  # noqa: E402


# ---------------------------------------------------------------- request/response

class DecideRequest(BaseModel):
    service: str = Field(..., description="Service name (informational)")
    current_limit_mb: float = Field(..., gt=0)
    current_usage_mb: float = Field(..., ge=0)
    usage_p50_mb: float | None = Field(None, ge=0)
    usage_p95_mb: float | None = Field(None, ge=0)
    usage_peak_mb: float | None = Field(None, ge=0)
    restart_count: int = Field(0, ge=0)
    traffic_qps: float = Field(0.0, ge=0)
    elapsed_steps: int = Field(0, ge=0, description="how many decision intervals into the episode")


class DecideResponse(BaseModel):
    recommended_limit_mb: int
    recommended_hpa_threshold: int = 70
    confidence: float
    reason: str
    policy: str  # "rl" | "heuristic"


# ---------------------------------------------------------------- app

app = FastAPI(title="AIOps RL Decide Service", version="0.1.0")
_AGENT: SAC | None = None
_CKPT_PATH: str | None = None


def _build_obs(req: DecideRequest) -> np.ndarray:
    """RightsizingEnv state encoding 과 동일하게 input vector 생성."""
    limit = max(req.current_limit_mb, 1e-6)
    p50 = req.usage_p50_mb if req.usage_p50_mb is not None else req.current_usage_mb
    p95 = req.usage_p95_mb if req.usage_p95_mb is not None else req.current_usage_mb
    peak = req.usage_peak_mb if req.usage_peak_mb is not None else req.current_usage_mb
    t = max(0, min(req.elapsed_steps, EPISODE_T - 1))
    obs = np.array([
        p50 / limit,
        p95 / limit,
        peak / limit,
        min(req.restart_count, 5) / 5.0,
        min(req.traffic_qps, 1000.0) / 1000.0,
        float(np.sin(2.0 * np.pi * t / EPISODE_T)),
        float(np.cos(2.0 * np.pi * t / EPISODE_T)),
        min(req.current_limit_mb, BASELINE_MAX_MB * 2) / BASELINE_MAX_MB,
    ], dtype=np.float32)
    return np.clip(obs, 0.0, 5.0)


def _heuristic(req: DecideRequest) -> DecideResponse:
    """RL 체크포인트 미존재 시 fallback. mockup 의 1.75x 휴리스틱과 비슷한 안전치."""
    new_limit = max(int(req.current_usage_mb * 2), int(req.current_limit_mb * 1.5))
    new_limit = int(np.clip(new_limit, BASELINE_MIN_MB, BASELINE_MAX_MB * 2))
    return DecideResponse(
        recommended_limit_mb=new_limit,
        recommended_hpa_threshold=70,
        confidence=0.6,
        reason="heuristic: max(usage * 2, current_limit * 1.5)",
        policy="heuristic",
    )


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "policy": "rl" if _AGENT is not None else "heuristic",
        "checkpoint": _CKPT_PATH,
    }


@app.post("/decide", response_model=DecideResponse)
def decide(req: DecideRequest) -> DecideResponse:
    if _AGENT is None:
        return _heuristic(req)

    obs = _build_obs(req)
    factor = float(_AGENT.select_action(obs, deterministic=True)[0])
    factor = float(np.clip(factor, ACT_LOW, ACT_HIGH))
    new_limit = int(np.clip(req.current_limit_mb * factor, BASELINE_MIN_MB, BASELINE_MAX_MB * 2))
    return DecideResponse(
        recommended_limit_mb=new_limit,
        recommended_hpa_threshold=70,
        confidence=0.85,
        reason=f"sac+reptile policy: factor={factor:.2f}",
        policy="rl",
    )


# ---------------------------------------------------------------- entrypoint

def _load_agent(ckpt_path: str | None) -> None:
    global _AGENT, _CKPT_PATH
    if ckpt_path and Path(ckpt_path).exists():
        try:
            _AGENT = SAC.load(ckpt_path)
            _CKPT_PATH = ckpt_path
            print(f"[serve] loaded RL checkpoint: {ckpt_path}", flush=True)
            return
        except Exception as e:  # noqa: BLE001
            print(f"[serve] failed to load checkpoint ({e}); falling back to heuristic",
                  flush=True)
    print("[serve] no RL checkpoint -> heuristic policy active", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ckpt", type=str, default=os.environ.get(
        "RL_CHECKPOINT", "checkpoints/sac_reptile.pt"))
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    _load_agent(args.ckpt)

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
