"""
Reptile + SAC 메타학습 entrypoint.

Usage (VM 안 venv 활성화 후):
    python train.py                            # 기본 30 outer iter
    python train.py --outer 50 --inner 2000    # 더 길게
    python train.py --device cpu --seed 7
출력 체크포인트: checkpoints/sac_reptile.pt
"""

import argparse
import os
import sys
from pathlib import Path

import torch

# 프로젝트 루트에서 실행하든 04-rl 안에서 실행하든 모두 동작하도록
sys.path.insert(0, str(Path(__file__).parent))

from env import RightsizingEnv, list_tasks  # noqa: E402
from agent import SAC, SACConfig, ReptileConfig, reptile_train  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--outer", type=int, default=30, help="Reptile outer iterations")
    p.add_argument("--inner", type=int, default=1500, help="SAC env steps per task")
    p.add_argument("--tasks-per-iter", type=int, default=4)
    p.add_argument("--meta-lr", type=float, default=0.3)
    p.add_argument("--hidden", type=int, default=64)
    p.add_argument("--batch", type=int, default=64)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--device", type=str, default="cpu", choices=["cpu", "cuda"])
    p.add_argument("--out", type=str, default="checkpoints/sac_reptile.pt")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # task pool 정보 출력
    print(f"task pool: {list_tasks()}")
    sample_env = RightsizingEnv()
    obs_dim = sample_env.observation_space.shape[0]
    act_dim = sample_env.action_space.shape[0]
    act_low = float(sample_env.action_space.low[0])
    act_high = float(sample_env.action_space.high[0])
    print(f"obs_dim={obs_dim} act_dim={act_dim} action_range=[{act_low}, {act_high}]")

    sac_cfg = SACConfig(
        obs_dim=obs_dim, act_dim=act_dim,
        act_low=act_low, act_high=act_high,
        hidden=args.hidden,
        batch_size=args.batch,
        device=args.device,
    )
    rep_cfg = ReptileConfig(
        outer_iters=args.outer,
        inner_steps=args.inner,
        tasks_per_iter=args.tasks_per_iter,
        meta_lr=args.meta_lr,
        seed=args.seed,
    )

    agent = reptile_train(sac_cfg, rep_cfg)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    agent.save(str(out_path))
    print(f"\nSaved checkpoint -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
