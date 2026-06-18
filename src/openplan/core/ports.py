from dataclasses import dataclass
from typing import Protocol


@dataclass
class CostProbeResult:
    tokens: int


class CostProbePort(Protocol):
    async def get_delta(self, from_timestamp: float, to_timestamp: float) -> CostProbeResult:
        ...


@dataclass
class MeshBaseline:
    match_level: str
    action: str
    phase_label_tokens: str
    avg_cost: float
    ci_lo: float
    ci_hi: float
    sample_count: int
    success_rate: float


class MeshPort(Protocol):
    async def push_checkpoints(self, checkpoints: list[dict]) -> bool:
        ...

    async def pull_baselines(self) -> list[MeshBaseline]:
        ...
