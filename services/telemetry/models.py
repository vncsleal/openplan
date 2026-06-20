from __future__ import annotations

from pydantic import BaseModel, Field


class CalibrationEvent(BaseModel):
    project_type: str = Field(
        default="", description="e.g. typescript_library, python_cli"
    )
    action: str = Field(description="e.g. implement, design, test")
    phase_label_tokens: str | None = Field(
        default=None, description="Tokenized phase label for match-level aggregation"
    )
    expected_cost: float | None = Field(
        default=None, description="Estimated cost in tokens"
    )
    actual_cost: float = Field(gt=0, description="Actual cost in tokens")
    outcome: str = Field(default="success", description="success, partial, failure")
    session_id: str = Field(default="")
    timestamp: float | None = Field(default=None)


class TelemetryBatch(BaseModel):
    events: list[CalibrationEvent] = Field(
        max_length=50, description="Up to 50 events per batch"
    )


class Baseline(BaseModel):
    project_type: str
    action: str
    match_level: str = Field(
        default="action", description="exact, label_keyword, or action"
    )
    cost_tokens: float
    sample_count: int
    p50: float
    p25: float | None = None
    p75: float | None = None


class CalibrationResponse(BaseModel):
    baselines: list[Baseline]


class HealthResponse(BaseModel):
    ok: bool
    events_count: int
    version: str
