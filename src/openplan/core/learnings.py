from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any


def learnings(conn: sqlite3.Connection) -> dict[str, Any]:
    type_action: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(dict))

    for r in conn.execute(
        "SELECT project_type, action, cost_tokens, sample_count FROM cost_baselines "
        "WHERE project IS NULL ORDER BY project_type, action"
    ).fetchall():
        pt = r["project_type"]
        action = r["action"]
        type_action[pt][action] = {
            "avg_cost": r["cost_tokens"],
            "samples": r["sample_count"],
        }

    action_accuracy: dict[str, dict[str, float]] = {}
    for r in conn.execute("SELECT key, value FROM meta WHERE key LIKE 'tuning:%'"):
        action_name = r["key"][7:]
        try:
            td = json.loads(r["value"])
            if td.get("count", 0) > 1:
                action_accuracy[action_name] = {
                    "avg_cost": td.get("avg_cost", 0),
                    "stddev": td.get("cost_stddev", 0) or 0,
                    "success_rate": td.get("success_rate", 0),
                    "samples": td.get("count", 0),
                }
        except (json.JSONDecodeError, TypeError):
            pass

    patterns: list[dict[str, Any]] = []
    for pt, actions in type_action.items():
        for action_name, info in actions.items():
            tuning = action_accuracy.get(action_name, {})
            stddev = tuning.get("stddev", 0) or 0
            avg = info["avg_cost"]
            cv = stddev / avg if avg > 0 else 0
            pattern: dict[str, Any] = {
                "project_type": pt,
                "action": action_name,
                "avg_cost": avg,
                "samples": info["samples"],
            }
            if tuning:
                pattern["stddev"] = round(stddev, 1)
                pattern["cv"] = round(cv, 4)
                pattern["success_rate"] = tuning.get("success_rate", 0)
                pattern["variability"] = "high" if cv > 1.0 else ("medium" if cv > 0.5 else "low")
                if cv > 1.0:
                    pattern["note"] = "highly variable — consider breaking into smaller steps"
            patterns.append(pattern)

    total_design = sum(
        p["avg_cost"] for p in patterns if p["action"] == "design"
    )
    total_implement = sum(
        p["avg_cost"] for p in patterns if p["action"] == "implement"
    )
    summary = {
        "patterns": patterns,
        "total_project_types": len(type_action),
        "total_patterns": len(patterns),
        "cross_type_averages": {
            "design": round(total_design / max(sum(1 for p in patterns if p["action"] == "design"), 1), 1),
            "implement": round(total_implement / max(sum(1 for p in patterns if p["action"] == "implement"), 1), 1),
        },
    }

    return summary
