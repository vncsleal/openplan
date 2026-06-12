from __future__ import annotations

import random
from typing import Any

ARMS: dict[str, dict[str, float]] = {
    "t03_p10": {"threshold": 0.3, "penalty": 1.0},
    "t03_p15": {"threshold": 0.3, "penalty": 1.5},
    "t03_p20": {"threshold": 0.3, "penalty": 2.0},
    "t05_p10": {"threshold": 0.5, "penalty": 1.0},
    "t05_p15": {"threshold": 0.5, "penalty": 1.5},
    "t05_p20": {"threshold": 0.5, "penalty": 2.0},
    "t07_p10": {"threshold": 0.7, "penalty": 1.0},
    "t07_p15": {"threshold": 0.7, "penalty": 1.5},
    "t07_p20": {"threshold": 0.7, "penalty": 2.0},
}


class ThompsonBandit:
    def __init__(self, arms: dict[str, dict[str, float]] | None = None) -> None:
        self._arms = dict(ARMS if arms is None else arms)
        self._alpha: dict[str, float] = {}
        self._beta: dict[str, float] = {}
        self._chosen_arm: str | None = None

    def pick_arm(self) -> str:
        scores: list[tuple[float, str]] = []
        for name in self._arms:
            a = self._alpha.get(name, 1)
            b = self._beta.get(name, 1)
            sample = random.betavariate(a, b)
            scores.append((sample, name))
        scores.sort(key=lambda x: -x[0])
        self._chosen_arm = scores[0][1]
        return self._chosen_arm

    def update(self, accepted: bool, reward_weight: float | None = None) -> None:
        if self._chosen_arm is None:
            return
        name = self._chosen_arm
        if accepted:
            increment = reward_weight if reward_weight is not None else 1.0
            self._alpha[name] = self._alpha.get(name, 1) + increment
        else:
            self._beta[name] = self._beta.get(name, 1) + 1

    @property
    def chosen_arm(self) -> str | None:
        return self._chosen_arm

    def get_arm_params(self, name: str) -> dict[str, float]:
        return dict(self._arms.get(name, self._arms.get("t05_p15", {"threshold": 0.5, "penalty": 1.5})))

    def serialize(self) -> dict[str, Any]:
        arms_data: dict[str, dict[str, float]] = {}
        for name in self._arms:
            arms_data[name] = {
                "alpha": self._alpha.get(name, 1),
                "beta": self._beta.get(name, 1),
            }
        return {"arms": arms_data, "chosen_arm": self._chosen_arm}

    @classmethod
    def deserialize(cls, data: dict[str, Any] | None) -> ThompsonBandit:
        bandit = cls()
        if not data:
            return bandit
        arms_data = data.get("arms", {})
        for name, ab in arms_data.items():
            bandit._alpha[name] = ab.get("alpha", 1)
            bandit._beta[name] = ab.get("beta", 1)
        bandit._chosen_arm = data.get("chosen_arm")
        return bandit
