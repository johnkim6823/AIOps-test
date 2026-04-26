"""
Soft Actor-Critic (SAC) for continuous control.

표준 SAC (Haarnoja et al., 2018):
- Stochastic Gaussian actor, tanh-squashed
- 두 개의 Q-critic + target networks (clipped double-Q)
- Replay buffer 에서 mini-batch 샘플
- 자동 alpha (entropy temperature) 학습 → target_entropy = -|A|

본 구현은 Reptile 메타 inner loop 에 호환되도록 다음을 보장:
- 모든 학습 가능 파라미터는 self.params 로 노출
- get_state_dict() / load_state_dict() 으로 weight 평균/이동 가능
"""

from __future__ import annotations

import copy
import math
from collections import deque
from dataclasses import dataclass

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


@dataclass
class SACConfig:
    obs_dim: int
    act_dim: int
    act_low: float
    act_high: float
    hidden: int = 64
    gamma: float = 0.99
    tau: float = 0.005          # target soft update rate
    lr_actor: float = 3e-4
    lr_critic: float = 3e-4
    lr_alpha: float = 3e-4
    batch_size: int = 64
    buffer_size: int = 50_000
    target_entropy: float | None = None  # default: -act_dim
    init_alpha: float = 0.2
    device: str = "cpu"


class ReplayBuffer:
    """단순 ring-buffer 형태의 replay buffer."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.buf: deque = deque(maxlen=capacity)

    def push(self, s, a, r, s_next, done):
        self.buf.append((np.asarray(s, dtype=np.float32),
                         np.asarray(a, dtype=np.float32),
                         float(r),
                         np.asarray(s_next, dtype=np.float32),
                         float(done)))

    def sample(self, batch: int) -> tuple[torch.Tensor, ...]:
        idx = np.random.randint(0, len(self.buf), size=batch)
        s, a, r, s2, d = zip(*[self.buf[i] for i in idx])
        return (
            torch.from_numpy(np.stack(s)),
            torch.from_numpy(np.stack(a)),
            torch.tensor(r, dtype=torch.float32).unsqueeze(1),
            torch.from_numpy(np.stack(s2)),
            torch.tensor(d, dtype=torch.float32).unsqueeze(1),
        )

    def __len__(self) -> int:
        return len(self.buf)


def _mlp(in_dim: int, out_dim: int, hidden: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(in_dim, hidden),
        nn.ReLU(),
        nn.Linear(hidden, hidden),
        nn.ReLU(),
        nn.Linear(hidden, out_dim),
    )


class GaussianActor(nn.Module):
    """tanh-squashed Gaussian policy. action = tanh(N(mu, sigma)) * scale + bias."""

    LOG_STD_MIN = -5.0
    LOG_STD_MAX = 2.0

    def __init__(self, obs_dim: int, act_dim: int, hidden: int,
                 act_low: float, act_high: float):
        super().__init__()
        self.body = nn.Sequential(
            nn.Linear(obs_dim, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
        )
        self.mu_head = nn.Linear(hidden, act_dim)
        self.log_std_head = nn.Linear(hidden, act_dim)
        # action 을 [low, high] 로 affine 변환
        self.register_buffer("act_scale", torch.tensor((act_high - act_low) / 2.0))
        self.register_buffer("act_bias", torch.tensor((act_high + act_low) / 2.0))

    def forward(self, s: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        h = self.body(s)
        mu = self.mu_head(h)
        log_std = self.log_std_head(h).clamp(self.LOG_STD_MIN, self.LOG_STD_MAX)
        return mu, log_std

    def sample(self, s: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """rsample → action, log_prob (squash 보정 포함), tanh 적용 전 mean."""
        mu, log_std = self.forward(s)
        std = log_std.exp()
        normal = torch.distributions.Normal(mu, std)
        u = normal.rsample()  # reparameterized
        t = torch.tanh(u)
        action = t * self.act_scale + self.act_bias
        # log_prob with tanh correction (Eq. 21 of SAC paper)
        log_prob = normal.log_prob(u) - torch.log(self.act_scale * (1 - t.pow(2)) + 1e-6)
        log_prob = log_prob.sum(dim=-1, keepdim=True)
        mean_action = torch.tanh(mu) * self.act_scale + self.act_bias
        return action, log_prob, mean_action


class QNetwork(nn.Module):
    def __init__(self, obs_dim: int, act_dim: int, hidden: int):
        super().__init__()
        self.net = _mlp(obs_dim + act_dim, 1, hidden)

    def forward(self, s: torch.Tensor, a: torch.Tensor) -> torch.Tensor:
        return self.net(torch.cat([s, a], dim=-1))


class SAC:
    """Soft Actor-Critic agent. inner-loop 안에서 학습 / Reptile 에서 weight 이동."""

    def __init__(self, cfg: SACConfig):
        self.cfg = cfg
        device = torch.device(cfg.device)
        self.device = device

        self.actor = GaussianActor(cfg.obs_dim, cfg.act_dim, cfg.hidden,
                                   cfg.act_low, cfg.act_high).to(device)
        self.q1 = QNetwork(cfg.obs_dim, cfg.act_dim, cfg.hidden).to(device)
        self.q2 = QNetwork(cfg.obs_dim, cfg.act_dim, cfg.hidden).to(device)
        self.q1_t = copy.deepcopy(self.q1).requires_grad_(False)
        self.q2_t = copy.deepcopy(self.q2).requires_grad_(False)

        # log_alpha 는 학습 가능한 스칼라
        self.log_alpha = torch.tensor(math.log(cfg.init_alpha), requires_grad=True, device=device)
        self.target_entropy = (cfg.target_entropy if cfg.target_entropy is not None
                               else -float(cfg.act_dim))

        self.opt_actor = torch.optim.Adam(self.actor.parameters(), lr=cfg.lr_actor)
        self.opt_q = torch.optim.Adam(
            list(self.q1.parameters()) + list(self.q2.parameters()), lr=cfg.lr_critic
        )
        self.opt_alpha = torch.optim.Adam([self.log_alpha], lr=cfg.lr_alpha)

        self.buffer = ReplayBuffer(cfg.buffer_size)

    # ---------------------------------------------------------------- act / update

    @torch.no_grad()
    def select_action(self, obs: np.ndarray, deterministic: bool = False) -> np.ndarray:
        s = torch.from_numpy(np.asarray(obs, dtype=np.float32)).unsqueeze(0).to(self.device)
        action, _, mean_action = self.actor.sample(s)
        a = mean_action if deterministic else action
        return a.squeeze(0).cpu().numpy().astype(np.float32)

    def update(self) -> dict[str, float]:
        if len(self.buffer) < self.cfg.batch_size:
            return {}

        s, a, r, s2, d = (t.to(self.device) for t in self.buffer.sample(self.cfg.batch_size))
        alpha = self.log_alpha.exp().detach()

        # ---- critic update ----
        with torch.no_grad():
            a2, logp2, _ = self.actor.sample(s2)
            q1_t = self.q1_t(s2, a2)
            q2_t = self.q2_t(s2, a2)
            q_t = torch.min(q1_t, q2_t) - alpha * logp2
            y = r + self.cfg.gamma * (1.0 - d) * q_t
        q1_loss = F.mse_loss(self.q1(s, a), y)
        q2_loss = F.mse_loss(self.q2(s, a), y)
        q_loss = q1_loss + q2_loss
        self.opt_q.zero_grad()
        q_loss.backward()
        self.opt_q.step()

        # ---- actor update ----
        a_new, logp, _ = self.actor.sample(s)
        q1_new = self.q1(s, a_new)
        q2_new = self.q2(s, a_new)
        q_new = torch.min(q1_new, q2_new)
        actor_loss = (alpha * logp - q_new).mean()
        self.opt_actor.zero_grad()
        actor_loss.backward()
        self.opt_actor.step()

        # ---- alpha update ----
        alpha_loss = -(self.log_alpha * (logp.detach() + self.target_entropy)).mean()
        self.opt_alpha.zero_grad()
        alpha_loss.backward()
        self.opt_alpha.step()

        # ---- target soft update ----
        with torch.no_grad():
            for p, pt in zip(self.q1.parameters(), self.q1_t.parameters()):
                pt.data.mul_(1.0 - self.cfg.tau).add_(self.cfg.tau * p.data)
            for p, pt in zip(self.q2.parameters(), self.q2_t.parameters()):
                pt.data.mul_(1.0 - self.cfg.tau).add_(self.cfg.tau * p.data)

        return {
            "q_loss": float(q_loss.item()),
            "actor_loss": float(actor_loss.item()),
            "alpha": float(alpha.item()),
            "alpha_loss": float(alpha_loss.item()),
        }

    # ---------------------------------------------------------------- weights I/O (Reptile 용)

    def get_meta_state_dict(self) -> dict[str, torch.Tensor]:
        """메타 학습 대상 weight 만 추출 (target net / optimizer / buffer 제외)."""
        return {
            **{f"actor.{k}": v.detach().clone() for k, v in self.actor.state_dict().items()},
            **{f"q1.{k}":    v.detach().clone() for k, v in self.q1.state_dict().items()},
            **{f"q2.{k}":    v.detach().clone() for k, v in self.q2.state_dict().items()},
            "log_alpha": self.log_alpha.detach().clone(),
        }

    def load_meta_state_dict(self, state: dict[str, torch.Tensor]) -> None:
        actor_state = {k.removeprefix("actor."): v for k, v in state.items() if k.startswith("actor.")}
        q1_state    = {k.removeprefix("q1."):    v for k, v in state.items() if k.startswith("q1.")}
        q2_state    = {k.removeprefix("q2."):    v for k, v in state.items() if k.startswith("q2.")}
        self.actor.load_state_dict(actor_state)
        self.q1.load_state_dict(q1_state)
        self.q2.load_state_dict(q2_state)
        # target nets 도 함께 동기화
        self.q1_t.load_state_dict(q1_state)
        self.q2_t.load_state_dict(q2_state)
        with torch.no_grad():
            self.log_alpha.copy_(state["log_alpha"])

    def save(self, path: str) -> None:
        torch.save({"meta_state": self.get_meta_state_dict(), "cfg": self.cfg.__dict__}, path)

    @classmethod
    def load(cls, path: str, map_location: str = "cpu") -> "SAC":
        ckpt = torch.load(path, map_location=map_location, weights_only=False)
        cfg = SACConfig(**ckpt["cfg"])
        agent = cls(cfg)
        agent.load_meta_state_dict(ckpt["meta_state"])
        return agent
