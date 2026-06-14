from __future__ import annotations

import json
import sqlite3
from typing import Any


def retro(project: str, conn: sqlite3.Connection) -> dict[str, Any]:
    acts = conn.execute(
        "SELECT payload FROM events WHERE project = ? AND event_type = 'acted' ORDER BY created_at ASC",
        (project,),
    ).fetchall()

    if not acts:
        return {"ok": False, "error": "No acted events found for project", "project": project}

    per_action: dict[str, list[dict[str, Any]]] = {}
    total_expected = 0.0
    total_actual = 0.0
    step_count = 0

    for row in acts:
        try:
            p = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        except (json.JSONDecodeError, TypeError):
            continue
        action = p.get("action", "unknown")
        expected = p.get("expected_cost", {})
        actual = p.get("cost_actual", {})
        exp_tokens = expected.get("tokens") if expected else 0
        act_tokens = actual.get("tokens", 0) if actual else 0
        if exp_tokens and act_tokens:
            per_action.setdefault(action, []).append({
                "expected": exp_tokens,
                "actual": act_tokens,
                "delta": act_tokens - exp_tokens,
                "accuracy": round(exp_tokens / act_tokens, 4) if act_tokens > 0 else 0,
            })
            total_expected += exp_tokens
            total_actual += act_tokens
            step_count += 1

    action_summary: dict[str, dict[str, Any]] = {}
    worst_offender = None
    worst_delta = 0.0
    best_estimator = None
    best_accuracy = 0.0

    for action_name, entries in per_action.items():
        avg_delta = sum(e["delta"] for e in entries) / len(entries)
        avg_accuracy = sum(e["accuracy"] for e in entries) / len(entries)
        total_act_sum = sum(e["actual"] for e in entries)
        total_exp_sum = sum(e["expected"] for e in entries)
        action_summary[action_name] = {
            "occurrences": len(entries),
            "avg_delta": round(avg_delta, 1),
            "avg_accuracy": round(avg_accuracy, 4),
            "total_expected": round(total_exp_sum, 1),
            "total_actual": round(total_act_sum, 1),
            "direction": "under" if avg_delta < 0 else "over",
        }
        if abs(avg_delta) > abs(worst_delta):
            worst_delta = avg_delta
            worst_offender = action_name
        if avg_accuracy > best_accuracy:
            best_accuracy = avg_accuracy
            best_estimator = action_name

    overall_accuracy = round(total_expected / total_actual, 4) if total_actual > 0 else 0

    return {
        "ok": True,
        "project": project,
        "total_steps": step_count,
        "total_expected_cost": round(total_expected, 1),
        "total_actual_cost": round(total_actual, 1),
        "overall_accuracy": overall_accuracy,
        "direction": "under" if total_actual < total_expected else ("over" if total_actual > total_expected else "exact"),
        "worst_offender": {"action": worst_offender, "avg_delta": round(worst_delta, 1)} if worst_offender else None,
        "best_estimator": {"action": best_estimator, "avg_accuracy": best_accuracy} if best_estimator else None,
        "per_action": action_summary,
    }
