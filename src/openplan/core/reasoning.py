from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any

REASONING_FIELDS = frozenset({
    "type", "question", "reasoning", "decision",
    "alternatives", "evidence", "conclusion", "tags",
})

VALID_TYPES = frozenset({
    "question", "hypothesis", "experiment", "decision", "action", "result",
})

STATUS_VALUES = frozenset({
    "pending", "in_progress", "done", "blocked", "superseded", "cascade_blocked",
})


@dataclass
class ReasoningPayload:
    type: str = "action"
    question: str = ""
    reasoning: str = ""
    decision: str = ""
    alternatives: list[str] = field(default_factory=list)
    evidence: str = ""
    conclusion: str = ""
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_props(cls, props: dict[str, Any]) -> ReasoningPayload:
        filtered = {k: v for k, v in props.items() if k in REASONING_FIELDS}
        return cls(**filtered)

    @classmethod
    def from_json(cls, raw: str) -> ReasoningPayload:
        try:
            data = json.loads(raw) if isinstance(raw, str) else {}
        except (json.JSONDecodeError, TypeError):
            data = {}
        return cls.from_props(data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def merge_into_props(self, existing_props: dict[str, Any]) -> dict[str, Any]:
        result = dict(existing_props)
        for k, v in asdict(self).items():
            if k in REASONING_FIELDS:
                result[k] = v
        return result

    def validate(self) -> None:
        if self.type not in VALID_TYPES:
            raise ValueError(f"Invalid reasoning type: {self.type}. Must be one of {sorted(VALID_TYPES)}")
