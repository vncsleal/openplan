from __future__ import annotations

import copy
import json
import os
from typing import Any

CONFIG_SCHEMA: dict[str, dict[str, Any]] = {
    "db_path": {"type": str, "default": "openplan.db"},
    "stale_days": {"type": int, "default": 2, "min": 1, "max": 365},
    "wip_limit": {"type": int, "default": 20, "min": 1, "max": 1000},
    "activation_threshold": {"type": float, "default": 0.5, "min": 0.0, "max": 1.0},
    "plan_limit": {"type": int, "default": 10, "min": 1, "max": 100},
    "expansion_limit": {"type": int, "default": 5, "min": 1, "max": 50},
    "avg_edge_cost": {"type": float, "default": 5000.0, "min": 100, "max": 1000000},
    "heuristic_scale": {"type": float, "default": 1.0, "min": 0.1, "max": 10.0},
    "reverse_penalty": {"type": float, "default": 3.0, "min": 1.0, "max": 100.0},
    "risk_adjustment": {"type": str, "default": "probability", "values": ["none", "probability", "variance"]},
    "tune_interval": {"type": int, "default": 10, "min": 0, "max": 1000},
    "activation_weights": {"type": dict, "default": {"in_degree": 0.33, "frontier": 0.24, "recency": 0.19, "boost": 0.09, "visit": 0.1, "novelty": 0.05}},
    "learning": {"type": dict, "default": {}},
    "embedding": {"type": dict, "default": {}},
    "page_rank": {"type": dict, "default": {}},
    "stale_branch_hours": {"type": int, "default": 24, "min": 1, "max": 720},
    "recommend_weights": {"type": dict, "default": {}},
    "maintenance_interval_minutes": {"type": int, "default": 5, "min": 1, "max": 1440},
    "adaptive_weights": {"type": dict, "default": {}},
    "insight_similarity_threshold": {"type": float, "default": 0.7, "min": 0.0, "max": 1.0},
    "telemetry_endpoint": {"type": str, "default": ""},
    "telemetry_enabled": {"type": bool, "default": False},
}


def _validate_config(raw: dict[str, Any]) -> dict[str, Any]:
    validated: dict[str, Any] = {}
    for key, value in raw.items():
        spec = CONFIG_SCHEMA.get(key)
        if not spec:
            raise ValueError(f"Unknown config key: '{key}'")
        expected_type = spec["type"]
        if not isinstance(value, expected_type):
            raise TypeError(f"Config '{key}' must be {expected_type.__name__}, got {type(value).__name__}")
        if expected_type in (int, float) and "min" in spec and value < spec["min"]:
            raise ValueError(f"Config '{key}' must be >= {spec['min']}, got {value}")
        if expected_type in (int, float) and "max" in spec and value > spec["max"]:
            raise ValueError(f"Config '{key}' must be <= {spec['max']}, got {value}")
        if "values" in spec and value not in spec["values"]:
            raise ValueError(f"Config '{key}' must be one of {spec['values']}, got '{value}'")
        validated[key] = value
    return validated


def load_config(config_path: str | None = None) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    for key, spec in CONFIG_SCHEMA.items():
        cfg[key] = copy.deepcopy(spec["default"])
    cfg["db_path"] = os.environ.get("OPENPLAN_DB_PATH", cfg.get("db_path", "openplan.db"))
    if config_path and os.path.exists(config_path):
        with open(config_path) as f:
            raw = json.load(f)
        validated = _validate_config(raw)
        for key in list(cfg.keys()):
            if key in validated and isinstance(validated[key], dict) and isinstance(cfg[key], dict):
                merged = dict(cfg[key])
                merged.update(validated[key])
                validated[key] = merged
        cfg.update(validated)
    return cfg
