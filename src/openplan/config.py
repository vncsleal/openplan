from __future__ import annotations

import json
import os
from typing import Any

_CONFIG_SCHEMA_KEYS: set[str] = {
    "db_path", "stale_days", "wip_limit", "activation_threshold",
    "plan_limit", "expansion_limit", "avg_edge_cost", "heuristic_scale",
    "reverse_penalty", "risk_adjustment", "tune_interval",
    "activation_weights", "learning", "embedding", "page_rank",
    "stale_branch_hours", "recommend_weights", "maintenance_interval_minutes",
    "adaptive_weights", "insight_similarity_threshold",
    "telemetry_enabled", "telemetry_endpoint",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "stale_days": 2,
    "reverse_penalty": 1.0,
    "activation_weights": {
        "in_degree": 0.33,
        "frontier": 0.24,
        "recency": 0.19,
        "boost": 0.09,
        "visit": 0.1,
        "novelty": 0.05,
    },
    "activation_threshold": 0.5,
}


def _validate_config(raw: dict[str, Any]) -> dict[str, Any]:
    unknown = [k for k in raw if k not in _CONFIG_SCHEMA_KEYS]
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown config keys: {keys}")
    return raw


def load_config(config_path: str | None = None) -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    cfg["db_path"] = os.environ.get("OPENPLAN_DB_PATH", "openplan.db")
    if config_path and os.path.exists(config_path):
        with open(config_path) as f:
            raw = json.load(f)
        _validate_config(raw)
        for key in list(cfg.keys()):
            if key in raw and isinstance(raw[key], dict) and isinstance(cfg[key], dict):
                merged = dict(cfg[key])
                merged.update(raw[key])
                raw[key] = merged
        cfg.update(raw)
    return cfg
