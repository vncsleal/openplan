from __future__ import annotations

import json
import logging
import random
import sqlite3
from typing import Any

_log = logging.getLogger("openplan.self_tune")

from openplan.core.bandit import ThompsonBandit

DEFAULT_WEIGHTS: dict[str, float] = {
    "in_degree": 0.33,
    "frontier": 0.24,
    "recency": 0.19,
    "boost": 0.09,
    "visit": 0.1,
    "novelty": 0.05,
}


def _optimize_weights(conn: sqlite3.Connection, config: dict[str, Any]) -> dict[str, float]:
    current_raw = conn.execute(
        "SELECT value FROM meta WHERE key = 'self_tuning:weight_config'"
    ).fetchone()
    current = dict(json.loads(current_raw["value"])) if current_raw else dict(DEFAULT_WEIGHTS)

    history_raw = conn.execute(
        "SELECT value FROM meta WHERE key = 'self_tuning:weight_history'"
    ).fetchone()
    weight_history: list[dict[str, Any]] = json.loads(history_raw["value"]) if history_raw else []

    acceptance_rate = config.get("_cached_acceptance_rate", 0.5)
    if not weight_history:
        weight_history.append({"weights": dict(current), "acceptance_rate": acceptance_rate})
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("self_tuning:weight_history", json.dumps(weight_history)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("self_tuning:weight_config", json.dumps(current)),
        )
        return current

    last = weight_history[-1]
    if acceptance_rate >= last.get("acceptance_rate", 0):
        keys = list(current.keys())
        k = random.choice(keys)
        current[k] = current.get(k, 0.33) * 1.2
        total = sum(current.values())
        for key in current:
            current[key] = round(current[key] / total, 4)
    else:
        current = dict(last["weights"])
        keys = list(current.keys())
        k = random.choice(keys)
        current[k] = current.get(k, 0.33) * 0.8
        total = sum(current.values())
        for key in current:
            current[key] = round(current[key] / total, 4)

    weight_history.append({"weights": dict(current), "acceptance_rate": acceptance_rate})
    if len(weight_history) > 5:
        weight_history = weight_history[-5:]

    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("self_tuning:weight_config", json.dumps(current)),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("self_tuning:weight_history", json.dumps(weight_history)),
    )

    return current


def run(config: dict[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
    adjustments: dict[str, Any] = {}

    baselines_raw = conn.execute(
        "SELECT value FROM meta WHERE key = 'self_tuning:baselines'"
    ).fetchone()
    baselines: dict[str, Any] = json.loads(baselines_raw["value"]) if baselines_raw else {}

    bandit_data = conn.execute(
        "SELECT value FROM meta WHERE key = 'self_tuning:bandit'"
    ).fetchone()
    bandit = ThompsonBandit.deserialize(
        json.loads(bandit_data["value"]) if bandit_data else None
    )

    action_penalties: dict[str, float] = {}
    for r in conn.execute("SELECT key, value FROM meta WHERE key LIKE 'tuning:%'"):
        action = r["key"][7:]
        try:
            data = json.loads(r["value"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        success_rate = data.get("success_rate", 0.5)
        prev = baselines.get(action, {})
        penalty = bandit.get_arm_params(bandit.chosen_arm or "t05_p15")["penalty"]
        if success_rate < 0.3:
            action_penalties[action] = penalty
        elif prev.get("success_rate", 0.5) < success_rate - 0.1:
            action_penalties[action] = 0.9
        else:
            action_penalties[action] = 1.0
        baselines[action] = {"success_rate": success_rate, "count": data.get("count", 0)}

    chosen_arm = bandit.pick_arm()
    arm_params = bandit.get_arm_params(chosen_arm)
    threshold_adjustments: dict[str, float] = {}
    threshold_adjustments["activation_threshold"] = arm_params["threshold"]

    optimized_weights = _optimize_weights(conn, config)

    adjustments = {
        "action_penalties": action_penalties,
        "threshold_adjustments": threshold_adjustments,
        "activation_weights": optimized_weights,
    }

    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("self_tuning:overrides", json.dumps(adjustments)),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("self_tuning:bandit", json.dumps(bandit.serialize())),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("self_tuning:baselines", json.dumps(baselines)),
    )

    history_raw = conn.execute(
        "SELECT value FROM meta WHERE key = 'self_tuning:history'"
    ).fetchone()
    history: list[dict[str, Any]] = json.loads(history_raw["value"]) if history_raw else []
    history.append({
        "action_penalties": action_penalties,
        "threshold_adjustments": threshold_adjustments,
        "calibration_rate": round(
            conn.execute(
                "SELECT COUNT(*) AS cnt FROM edges WHERE weight_history IS NOT NULL AND weight_history != '[]'"
            ).fetchone()["cnt"]
            / max(
                conn.execute("SELECT COUNT(*) AS cnt FROM edges").fetchone()["cnt"], 1
            ),
            4,
        ),
        "bandit_arm": chosen_arm,
    })
    if len(history) > 20:
        history = history[-20:]
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("self_tuning:history", json.dumps(history)),
    )

    return {
        "ok": True,
        "action_penalties": action_penalties,
        "threshold_adjustments": threshold_adjustments,
        "calibration_rate": round(
            conn.execute(
                "SELECT COUNT(*) AS cnt FROM edges WHERE weight_history IS NOT NULL AND weight_history != '[]'"
            ).fetchone()["cnt"]
            / max(
                conn.execute("SELECT COUNT(*) AS cnt FROM edges").fetchone()["cnt"], 1
            ),
            4,
        ),
        "history_length": len(history),
        "bandit_arm": chosen_arm,
    }
