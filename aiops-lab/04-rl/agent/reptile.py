"""
Reptile meta-learning wrapper around SAC.

핵심 절차 (Nichol et al., 2018, "On First-Order Meta-Learning Algorithms"):

    init meta-params θ
    for outer_iter = 1..N_outer:
        sample tasks τ_1, ..., τ_K from task distribution
        for each task τ_i:
            θ_i ← θ
            run SAC inner-loop on env(τ_i) for N_inner steps     (θ_i 변경)
            collect θ_i
        θ ← θ + ε * (mean(θ_i) - θ)                              # Reptile update

Reptile 의 매력은 inner loop 가 어떤 방식이든 (SGD/Adam/SAC/PPO …) 상관 없이
"학습 후 weight 의 평균을 향해 한 step 이동" 한다는 점. SAC 의 actor/critic
모두 동일한 평균-이동 규칙을 받는다.

본 모듈은 'SAC 객체' 를 task 별로 1개씩 새로 만들지 않고,
하나의 SAC 인스턴스를 재사용하면서 meta-state 를 저장/복원한다 (메모리 절약).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch

from env import RightsizingEnv, list_tasks
from .sac import SAC, SACConfig


@dataclass
class ReptileConfig:
    outer_iters: int = 30           # 메타 업데이트 횟수
    inner_steps: int = 1500         # 각 task 에서 SAC 환경 step 수
    tasks_per_iter: int = 4         # 한 outer iter 에서 샘플할 task 수 (<=|task pool|)
    meta_lr: float = 0.3            # Reptile epsilon (0.1~0.5 권장)
    warmup_random_steps: int = 500  # 처음엔 buffer 채우기용 랜덤 행동
    update_every: int = 1           # env step 당 SAC update 호출 횟수
    eval_every: int = 5             # outer iter 단위 평가 주기
    eval_episodes: int = 1          # 평가 시 task 당 몇 에피소드
    seed: int = 42


def _interp_state_dicts(
    base: dict[str, torch.Tensor],
    others: list[dict[str, torch.Tensor]],
    eps: float,
) -> dict[str, torch.Tensor]:
    """θ ← θ + ε (mean(others) - θ). 모든 entry 에 동일하게 적용."""
    new = {}
    for k, base_v in base.items():
        stacked = torch.stack([o[k] for o in others], dim=0)
        mean_v = stacked.mean(dim=0)
        new[k] = base_v + eps * (mean_v - base_v)
    return new


def _run_inner_sac(
    agent: SAC,
    env: RightsizingEnv,
    inner_steps: int,
    warmup_random_steps: int,
    update_every: int,
    rng: np.random.Generator,
) -> dict[str, float]:
    """Hot-start 한 SAC 로 단일 task 에서 inner_steps 만큼 학습."""
    obs, _ = env.reset()
    ep_reward = 0.0
    ep_oom = 0
    ep_count = 0
    total_reward = 0.0
    total_oom = 0
    step = 0
    # 임시 fresh buffer 사용: meta-loop 사이 task 가 다르므로 신선한 데이터로 채움
    agent.buffer.buf.clear()

    while step < inner_steps:
        if step < warmup_random_steps:
            a = rng.uniform(env.action_space.low, env.action_space.high).astype(np.float32)
        else:
            a = agent.select_action(obs)

        obs2, r, term, trunc, info = env.step(a)
        done = float(term)  # truncate 는 bootstrap 그대로 (timeout limit)
        agent.buffer.push(obs, a, r, obs2, done)
        obs = obs2
        ep_reward += r
        ep_oom += int(info["oom"])
        step += 1

        for _ in range(update_every):
            agent.update()

        if term or trunc:
            total_reward += ep_reward
            total_oom += ep_oom
            ep_count += 1
            ep_reward = 0.0
            ep_oom = 0
            obs, _ = env.reset()

    return {
        "task_id": env.task_id,
        "episodes": ep_count,
        "mean_reward": (total_reward / max(ep_count, 1)),
        "mean_oom": (total_oom / max(ep_count, 1)),
    }


def _evaluate(agent: SAC, tasks: list[str], episodes: int, seed: int) -> dict[str, dict[str, float]]:
    """deterministic policy 로 task 별 평가."""
    out: dict[str, dict[str, float]] = {}
    for tid in tasks:
        env = RightsizingEnv(task_id=tid, seed=seed)
        rewards: list[float] = []
        ooms: list[int] = []
        for _ in range(episodes):
            obs, _ = env.reset()
            ep_r, ep_o = 0.0, 0
            done = False
            while not done:
                a = agent.select_action(obs, deterministic=True)
                obs, r, term, trunc, info = env.step(a)
                ep_r += r
                ep_o += int(info["oom"])
                done = term or trunc
            rewards.append(ep_r)
            ooms.append(ep_o)
        out[tid] = {"reward": float(np.mean(rewards)), "oom": float(np.mean(ooms))}
    return out


def reptile_train(
    sac_cfg: SACConfig,
    rep_cfg: ReptileConfig,
    log_fn=print,
) -> SAC:
    """Reptile + SAC 메타학습. 학습된 SAC agent 반환."""
    rng = np.random.default_rng(rep_cfg.seed)
    torch.manual_seed(rep_cfg.seed)

    agent = SAC(sac_cfg)
    meta_state = agent.get_meta_state_dict()
    task_pool = list_tasks()

    for it in range(1, rep_cfg.outer_iters + 1):
        sampled = list(rng.choice(task_pool,
                                  size=min(rep_cfg.tasks_per_iter, len(task_pool)),
                                  replace=False))

        task_states: list[dict[str, torch.Tensor]] = []
        task_logs: list[dict[str, float]] = []
        for tid in sampled:
            agent.load_meta_state_dict(meta_state)  # θ_i ← θ
            env = RightsizingEnv(task_id=str(tid), seed=int(rng.integers(0, 1_000_000)))
            stats = _run_inner_sac(
                agent=agent,
                env=env,
                inner_steps=rep_cfg.inner_steps,
                warmup_random_steps=rep_cfg.warmup_random_steps,
                update_every=rep_cfg.update_every,
                rng=rng,
            )
            task_logs.append(stats)
            task_states.append(agent.get_meta_state_dict())

        # Reptile meta-update
        meta_state = _interp_state_dicts(meta_state, task_states, eps=rep_cfg.meta_lr)
        agent.load_meta_state_dict(meta_state)

        log_fn(f"[outer {it:3d}/{rep_cfg.outer_iters}] tasks={','.join(map(str, sampled))}  "
               + " ".join(
                   f"{l['task_id']}:r={l['mean_reward']:7.2f}/oom={l['mean_oom']:.1f}"
                   for l in task_logs
               ))

        if rep_cfg.eval_every and it % rep_cfg.eval_every == 0:
            evals = _evaluate(agent, task_pool, rep_cfg.eval_episodes, seed=rep_cfg.seed + it)
            log_fn("    [eval] " + " ".join(
                f"{tid}:r={v['reward']:7.2f}/oom={v['oom']:.1f}" for tid, v in evals.items()
            ))

    return agent
