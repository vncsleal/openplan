from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from typing import Any

_log = logging.getLogger("openplan.learning")


def tune(conn: sqlite3.Connection, config: dict[str, Any]) -> dict[str, Any]:
    action_stats: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0, "total_cost": 0.0, "total_risk": 0.0, "success_count": 0})

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
            if outcome == "success":
                s["success_count"] += 1

    recommendations: dict[str, Any] = {}
    for action, s in action_stats.items():
        avg_cost = round(s["total_cost"] / s["count"], 1) if s["count"] > 0 else 0.0
        avg_risk = round(s["total_risk"] / s["count"], 4) if s["count"] > 0 else 0.0
        success_rate = round(s["success_count"] / s["count"], 4) if s["count"] > 0 else 0.0
        recommendations[action] = {
            "count": s["count"],
            "avg_cost": avg_cost,
            "avg_risk": avg_risk,
            "success_rate": success_rate,
        }
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (f"tuning:{action}", json.dumps(recommendations[action])),
        )

    if recommendations:
        conn.commit()

    return {
        "ok": True,
        "actions_tuned": len(recommendations),
        "recommendations": recommendations,
    }
