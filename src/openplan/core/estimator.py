from __future__ import annotations

import json
import math
import sqlite3
from typing import Any


def estimate(project_type: str, goal: str, conn: sqlite3.Connection) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    for r in conn.execute(
        "SELECT action, cost_tokens, cost_risk, sample_count FROM cost_baselines "
        "WHERE project IS NULL AND project_type = ? ORDER BY sample_count DESC",
        (project_type,),
    ).fetchall():
        actions.append({
            "action": r["action"],
            "avg_cost": r["cost_tokens"],
            "risk": r["cost_risk"],
            "samples": r["sample_count"],
        })

    action_variance: dict[str, dict[str, Any]] = {}
    for r in conn.execute("SELECT key, value FROM meta WHERE key LIKE 'tuning:%'"):
        action_name = r["key"][7:]
        try:
            td = json.loads(r["value"])
            if td.get("count", 0) > 1:
                action_variance[action_name] = {
                    "avg_cost": td.get("avg_cost", 0),
                    "stddev": td.get("cost_stddev", 0),
                    "ci_95": td.get("cost_ci_95"),
                    "samples": td.get("count", 0),
                    "success_rate": td.get("success_rate", 0),
                }
        except (json.JSONDecodeError, TypeError):
            pass

    risk_assessment: dict[str, str] = {}
    high_risk: list[str] = []
    for action_name, info in action_variance.items():
        stddev = info.get("stddev", 0) or 0
        avg = info.get("avg_cost", 0) or 1
        cv = stddev / avg if avg > 0 else 0
        if cv > 1.0:
            risk_assessment[action_name] = "high"
            high_risk.append(action_name)
        elif cv > 0.5:
            risk_assessment[action_name] = "medium"
        else:
            risk_assessment[action_name] = "low"

    total_estimate = sum(a["avg_cost"] for a in actions)
    total_variance = sum(
        (action_variance.get(a["action"], {}).get("stddev", 0) or 0) ** 2
        for a in actions
    )
    total_ci = None
    if total_variance > 0:
        std = math.sqrt(total_variance)
        total_ci = [round(max(0, total_estimate - 1.96 * std), 1),
                    round(total_estimate + 1.96 * std, 1)]

    return {
        "project_type": project_type,
        "goal": goal,
        "estimated_actions": actions,
        "action_variance": action_variance,
        "risk_assessment": risk_assessment,
        "high_risk_actions": high_risk,
        "total_estimated_cost": round(total_estimate, 1),
        "total_confidence_interval": total_ci,
        "baseline_sources": len(actions),
    }
