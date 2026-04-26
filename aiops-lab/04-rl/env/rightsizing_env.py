"""
RightsizingEnv - synthetic memory rightsizing environment for SAC + Reptile training.

각 episode 는 하나의 service profile (task) 위에서 진행된다. agent 는
"current limit 을 몇 배로 조정할지" (continuous factor in [0.5, 3.0]) 를 결정하고,
환경은 합성 워크로드 곡선에 따라 다음 step 의 메모리 사용량을 시뮬레이션한다.
사용량이 limit 을 초과하면 OOMKilled 이벤트로 처리되어 큰 음의 reward 를 받고
restart 카운터가 증가한다.

State space (8-dim, all in [0, ~2]):
    [0] usage_p50  / current_limit          (최근 10 step 사용량 50pct 비율)
    [1] usage_p95  / current_limit
    [2] usage_peak / current_limit
    [3] restart_count / 5                    (이번 에피소드 누적 OOM)
    [4] traffic_qps / 1000                   (트래픽 정규화)
    [5] sin(2 pi t / T)                      (시간 cyclic feature)
    [6] cos(2 pi t / T)
    [7] current_limit / BASELINE_MAX         (현재 limit 정규화)

Action space (1-dim):
    a[0] = limit_factor in [0.5, 3.0]        (다음 step 의 limit 배율)

Reward (per step):
    reward = -0.01 * cost_factor
             - 50.0 * is_oom
             + 1.0  * is_stable
    cost_factor = current_limit_mb / BASELINE_MIN_MB
    is_stable   = (0.4 < utilization < 0.85) and not is_oom

Episode terminates on:
    - 5 회 OOM 발생 (Pod give-up)
    - 50 step 도달 (자연스러운 truncate)
"""

from __future__ import annotations

import numpy as np
from typing import Any

try:
    import gymnasium as gym
    from gymnasium import spaces
    _HAS_GYM = True
except ImportError:  # 환경에 gymnasium 미설치 시도 가능하도록 fallback
    _HAS_GYM = False
    gym = None  # type: ignore
    spaces = None  # type: ignore


# 4개 합성 서비스 프로필. 새 task 는 여기 dict 만 추가하면 된다.
TASK_PROFILES: dict[str, dict[str, float]] = {
    # JVM 배치: 평소 30% / 주기적 spike 95%
    "jvm-batch":  {"base_ratio": 0.30, "spike_amp": 0.65, "spike_period": 10.0,
                   "leak_rate": 0.000, "noise": 0.05, "true_max_mb": 800.0},
    # Python 메모리 누수: 시간당 점진 증가
    "py-leak":    {"base_ratio": 0.25, "spike_amp": 0.00, "spike_period": 0.0,
                   "leak_rate": 0.012, "noise": 0.03, "true_max_mb": 600.0},
    # Go 안정형: 거의 일정
    "go-steady":  {"base_ratio": 0.40, "spike_amp": 0.05, "spike_period": 7.0,
                   "leak_rate": 0.000, "noise": 0.02, "true_max_mb": 400.0},
    # Node 이벤트 burst: 짧은 주기 spike
    "node-burst": {"base_ratio": 0.35, "spike_amp": 0.45, "spike_period": 5.0,
                   "leak_rate": 0.000, "noise": 0.08, "true_max_mb": 700.0},
}

BASELINE_MIN_MB = 64.0
BASELINE_MAX_MB = 1024.0
EPISODE_T = 50
HISTORY_WIN = 10
MAX_RESTARTS = 5

OBS_DIM = 8
ACT_DIM = 1
ACT_LOW = 0.5
ACT_HIGH = 3.0


class RightsizingEnv(gym.Env if _HAS_GYM else object):  # type: ignore[misc]
    """Synthetic Pod memory-rightsizing MDP. See module docstring."""

    metadata = {"render_modes": []}

    def __init__(self, task_id: str | None = None, seed: int | None = None):
        if _HAS_GYM:
            self.observation_space = spaces.Box(
                low=0.0, high=2.0, shape=(OBS_DIM,), dtype=np.float32
            )
            self.action_space = spaces.Box(
                low=np.array([ACT_LOW], dtype=np.float32),
                high=np.array([ACT_HIGH], dtype=np.float32),
                dtype=np.float32,
            )
        self._rng = np.random.default_rng(seed)
        self._fixed_task_id = task_id  # None 이면 reset 마다 랜덤 추첨
        self._init_episode(task_id=task_id)

    # ------------------------------------------------------------------ private

    def _pick_task(self, task_id: str | None) -> str:
        if task_id is not None:
            if task_id not in TASK_PROFILES:
                raise KeyError(f"unknown task_id: {task_id}")
            return task_id
        if self._fixed_task_id is not None:
            return self._fixed_task_id
        return str(self._rng.choice(list(TASK_PROFILES.keys())))

    def _init_episode(self, task_id: str | None = None) -> None:
        self.task_id = self._pick_task(task_id)
        self.profile = TASK_PROFILES[self.task_id]
        self.t = 0
        # 시작 limit 은 256MB (실제 운영의 흔한 default 값)
        self.current_limit_mb: float = 256.0
        self.restart_count = 0
        self.steps_since_oom = EPISODE_T
        self.usage_history: list[float] = []
        self.traffic_qps = float(self._rng.uniform(50.0, 500.0))
        self._traffic_drift = float(self._rng.uniform(-2.0, 2.0))

    def _next_true_usage_mb(self) -> float:
        """프로필 기반 합성 사용량. limit 과 무관하게 워크로드가 '필요로 하는' 메모리."""
        p = self.profile
        t = self.t
        ratio = p["base_ratio"]
        if p["spike_amp"] > 0 and p["spike_period"] > 0:
            # sin^2 → 0 ~ 1 사이 부드러운 spike
            phase = 2.0 * np.pi * t / p["spike_period"]
            ratio += p["spike_amp"] * float(np.sin(phase)) ** 2
        ratio += p["leak_rate"] * t  # 시간 경과 누적 leak
        ratio += float(self._rng.normal(0.0, p["noise"]))
        ratio = float(np.clip(ratio, 0.05, 1.5))  # 음수/극단치 컷
        return ratio * float(p["true_max_mb"])

    def _observe(self) -> np.ndarray:
        if self.usage_history:
            arr = np.asarray(self.usage_history, dtype=np.float64)
        else:
            arr = np.array([0.0])
        limit = max(self.current_limit_mb, 1e-6)
        p50 = float(np.percentile(arr, 50))
        p95 = float(np.percentile(arr, 95))
        peak = float(np.max(arr))
        s = np.array([
            p50 / limit,
            p95 / limit,
            peak / limit,
            self.restart_count / float(MAX_RESTARTS),
            self.traffic_qps / 1000.0,
            float(np.sin(2.0 * np.pi * self.t / EPISODE_T)),
            float(np.cos(2.0 * np.pi * self.t / EPISODE_T)),
            self.current_limit_mb / BASELINE_MAX_MB,
        ], dtype=np.float32)
        # 안전장치: 비정상 값 클립
        return np.clip(s, 0.0, 5.0).astype(np.float32)

    # ------------------------------------------------------------------ public

    def reset(
        self,
        *,
        seed: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        task_id = (options or {}).get("task_id")
        self._init_episode(task_id=task_id)
        return self._observe(), {"task_id": self.task_id}

    def step(
        self, action: np.ndarray
    ) -> tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        # 1) 액션 적용: limit_factor 로 limit 갱신
        factor = float(np.clip(action[0], ACT_LOW, ACT_HIGH))
        new_limit = self.current_limit_mb * factor
        self.current_limit_mb = float(np.clip(new_limit, BASELINE_MIN_MB, BASELINE_MAX_MB * 2.0))

        # 2) 워크로드 발생
        true_usage = self._next_true_usage_mb()

        # 3) OOM 판정
        oom = true_usage > self.current_limit_mb
        if oom:
            self.restart_count += 1
            self.steps_since_oom = 0
            # Pod restart → 사용량 base 로 회복
            actual_usage = min(self.current_limit_mb,
                               self.profile["base_ratio"] * self.profile["true_max_mb"])
        else:
            actual_usage = true_usage
            self.steps_since_oom += 1

        self.usage_history.append(actual_usage)
        if len(self.usage_history) > HISTORY_WIN:
            self.usage_history.pop(0)

        # 4) 트래픽 자연 변동 (다음 step 관측에 사용)
        self.traffic_qps = float(np.clip(
            self.traffic_qps + self._traffic_drift + self._rng.normal(0.0, 5.0),
            10.0, 1000.0,
        ))

        # 5) reward
        cost_factor = self.current_limit_mb / BASELINE_MIN_MB
        utilization = actual_usage / max(self.current_limit_mb, 1e-6)
        is_stable = (0.4 < utilization < 0.85) and (not oom)
        reward = (-0.01 * cost_factor) + (-50.0 * float(oom)) + (1.0 * float(is_stable))

        # 6) advance
        self.t += 1
        terminated = self.restart_count >= MAX_RESTARTS
        truncated = self.t >= EPISODE_T

        info = {
            "task_id": self.task_id,
            "oom": bool(oom),
            "actual_usage_mb": float(actual_usage),
            "true_usage_mb": float(true_usage),
            "limit_mb": float(self.current_limit_mb),
            "utilization": float(utilization),
            "restart_count": int(self.restart_count),
        }
        return self._observe(), float(reward), terminated, truncated, info

    def render(self) -> None:  # pragma: no cover
        return None

    def close(self) -> None:  # pragma: no cover
        return None


def list_tasks() -> list[str]:
    """지원 task id 목록."""
    return list(TASK_PROFILES.keys())
