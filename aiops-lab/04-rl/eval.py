"""
학습된 SAC + Reptile 체크포인트 평가.

Usage:
    python eval.py --ckpt checkpoints/sac_reptile.pt --episodes 5
"""

import argparse
import sys
from pathlib import Path
from statistics import mean, stdev

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from env import RightsizingEnv, list_tasks  # noqa: E402
from agent.sac import SAC  # noqa: E402


def evaluate_task(agent: SAC, task_id: str, episodes: int, seed: int) -> dict[str, float]:
    rewards: list[float] = []
    ooms: list[int] = []
    final_limits: list[float] = []
    survived: list[int] = []
    for ep in range(episodes):
        env = RightsizingEnv(task_id=task_id, seed=seed + ep)
        obs, _ = env.reset()
        ep_r, ep_o = 0.0, 0
        info = {"limit_mb": 0.0, "restart_count": 0}
        done = False
        while not done:
            a = agent.select_action(obs, deterministic=True)
            obs, r, term, trunc, info = env.step(a)
            ep_r += r
            ep_o += int(info["oom"])
            done = term or trunc
        rewards.append(ep_r)
        ooms.append(ep_o)
        final_limits.append(float(info["limit_mb"]))
        survived.append(0 if info["restart_count"] >= 5 else 1)
    return {
        "reward_mean": mean(rewards),
        "reward_std":  stdev(rewards) if len(rewards) > 1 else 0.0,
        "oom_mean":    mean(ooms),
        "limit_mean":  mean(final_limits),
        "survival":    mean(survived),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ckpt", type=str, default="checkpoints/sac_reptile.pt")
    p.add_argument("--episodes", type=int, default=5)
    p.add_argument("--seed", type=int, default=123)
    args = p.parse_args()

    if not Path(args.ckpt).exists():
        print(f"ERROR: checkpoint not found: {args.ckpt}", file=sys.stderr)
        return 1

    agent = SAC.load(args.ckpt)
    print(f"Loaded {args.ckpt}\n")

    print(f"{'task':<14}{'reward':>14}{'oom':>8}{'final_mb':>12}{'survival':>12}")
    print("-" * 60)
    overall_reward: list[float] = []
    overall_oom: list[float] = []
    for tid in list_tasks():
        m = evaluate_task(agent, tid, args.episodes, args.seed)
        print(f"{tid:<14}"
              f"{m['reward_mean']:>9.2f}+-{m['reward_std']:.1f}"
              f"{m['oom_mean']:>8.1f}"
              f"{m['limit_mean']:>12.0f}"
              f"{m['survival']*100:>11.0f}%")
        overall_reward.append(m["reward_mean"])
        overall_oom.append(m["oom_mean"])
    print("-" * 60)
    print(f"{'OVERALL':<14}{np.mean(overall_reward):>14.2f}{np.mean(overall_oom):>8.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
