from __future__ import annotations

import json
import os
from typing import Any


DEFAULT_CONFIG = {
    "stale_days": 2,
    "activation_weights": {
        "in_degree": 0.4,
        "frontier": 0.3,
        "recency": 0.2,
        "boost": 0.1,
    },
    "activation_threshold": 0.5,
}


def load_config(config_path: str | None = None) -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    cfg["db_path"] = os.environ.get("OPENPLAN_DB_PATH", "openplan.db")
    if config_path and os.path.exists(config_path):
        with open(config_path) as f:
            raw = json.load(f)
        cfg.update(raw)
    return cfg
