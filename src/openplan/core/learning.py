from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections import defaultdict
from typing import Any

_log = logging.getLogger("openplan.learning")

from openplan.core.bandit import ThompsonBandit
from openplan.core.self_tune import run as _self_tune


def _compute_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"stddev": 0.0, "variance": 0.0, "ci_95": None}
    n = len(values)
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    stddev = math.sqrt(variance)
    if n >= 2:
        stderr = stddev / math.sqrt(n)
        margin = 1.96 * stderr
        ci_95 = [round(mean - margin, 1), round(mean + margin, 1)]
    else:
        ci_95 = None
    return {"stddev": round(stddev, 1), "variance": round(variance, 1), "ci_95": ci_95}


def tune(conn: sqlite3.Connection, config: dict[str, Any]) -> dict[str, Any]:
    action_stats: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0, "total_cost": 0.0, "total_risk": 0.0, "success_count": 0})
    cost_samples: dict[str, list[float]] = defaultdict(list)

    for r in conn.execute(
        "SELECT e.action, e.cost_tokens, e.cost_risk, e.weight_history FROM edges e"
    ).fetchall():
        action = r["action"]
        s = action_stats[action]
        s["count"] += 1
        s["total_cost"] += r["cost_tokens"]
        s["total_risk"] += r["cost_risk"]

        wh_raw = r["weight_history"] or "[]"
        try:
            wh = json.loads(wh_raw) if isinstance(wh_raw, str) else wh_raw
        except (json.JSONDecodeError, TypeError):
            wh = []
        for entry in wh:
            if entry.get("auto"):
                continue
            outcome = entry.get("outcome", "")
            actual_cost = entry.get("actual_cost", {}).get("tokens")
            if actual_cost is not None:
                cost_samples[action].append(float(actual_cost))
            if outcome == "success":
                s["success_count"] += 1

    recommendations: dict[str, Any] = {}
    for action, s in action_stats.items():
        avg_cost = round(s["total_cost"] / s["count"], 1) if s["count"] > 0 else 0.0
        avg_risk = round(s["total_risk"] / s["count"], 4) if s["count"] > 0 else 0.0
        success_rate = round(s["success_count"] / s["count"], 4) if s["count"] > 0 else 0.0
        stats = _compute_stats(cost_samples.get(action, []))
        recommendations[action] = {
            "count": s["count"],
            "avg_cost": avg_cost,
            "avg_risk": avg_risk,
            "success_rate": success_rate,
            "cost_stddev": stats["stddev"],
            "cost_variance": stats["variance"],
            "cost_ci_95": stats["ci_95"],
        }
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (f"tuning:{action}", json.dumps(recommendations[action])),
        )

    if recommendations:
        conn.commit()

    bandit_data = conn.execute(
        "SELECT value FROM meta WHERE key = 'self_tuning:bandit'"
    ).fetchone()
    bandit = ThompsonBandit.deserialize(
        json.loads(bandit_data["value"]) if bandit_data else None
    )

    last_tune = conn.execute(
        "SELECT value FROM meta WHERE key = 'self_tuning:last_run'"
    ).fetchone()
    last_run = json.loads(last_tune["value"]) if last_tune else None

    recent_acts = conn.execute(
        "SELECT node_id, payload FROM events WHERE event_type = 'acted'"
        + (" AND created_at > ?" if last_run else "")
        + " ORDER BY created_at ASC",
        (last_run,) if last_run else (),
    ).fetchall()

    for evt in recent_acts:
        try:
            raw = evt["payload"]
            payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("action") and payload.get("target"):
            actual = payload.get("cost_actual", {}).get("tokens", 0)
            expected = payload.get("expected_cost", {}).get("tokens")
            if expected and expected > 0 and actual > 0:
                efficiency = expected / actual
                if efficiency >= 1.2:
                    rw = 1.0
                elif efficiency >= 0.9:
                    rw = 0.5
                else:
                    rw = 0.1
                bandit.update(accepted=True, reward_weight=rw)
            else:
                bandit.update(accepted=True)

    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("self_tuning:bandit", json.dumps(bandit.serialize())),
    )

    from datetime import datetime, timezone
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("self_tuning:last_run", json.dumps(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))),
    )

    try:
        self_tune_result = _self_tune(config, conn)
        conn.commit()
    except Exception:
        _log.exception("self_tune failed")
        self_tune_result = {}

    return {
        "ok": True,
        "actions_tuned": len(recommendations),
        "recommendations": recommendations,
        "self_tuning": self_tune_result.get("bandit_arm")
        or self_tune_result.get("threshold_adjustments")
        or None,
    }
