from __future__ import annotations

import sqlite3
from typing import Any

from openplan.core.errors import NoPathError
from openplan.core.planner import plan as _plan
from openplan.core.resolve import resolve_target


def simulate(
    project: str,
    sequence: list[dict[str, Any]],
    conn: sqlite3.Connection,
    config: dict[str, Any],
    cursor: str | None = None,
) -> dict[str, Any]:
    if not sequence:
        return {"ok": False, "error": "Sequence must have at least one step", "project": project}

    if not cursor:
        root = conn.execute(
            "SELECT id FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
            (project,),
        ).fetchone()
        cursor = root["id"] if root else None

    if not cursor:
        return {"ok": False, "error": "No cursor or root found for project", "project": project}

    trajectory = []
    total_cost = 0.0
    cum_prob = 1.0
    current = cursor

    for i, step in enumerate(sequence):
        action = step.get("action", "implement")
        target_desc = step.get("target", "")

        if not target_desc:
            return {"ok": False, "error": f"Step {i}: target is required", "project": project}

        results = resolve_target(target_desc, project, conn, top_k=1)
        if not results:
            return {"ok": False, "error": f"Step {i}: could not resolve '{target_desc}'", "project": project}

        target_id = results[0]["id"]

        try:
            plan_result = _plan(current, target_id, conn, config)
        except NoPathError:
            return {"ok": False, "error": f"Step {i}: no path from {current} to {target_id}", "project": project}

        step_cost = plan_result.get("expected_cost", {}).get("tokens", 0)
        step_prob = plan_result.get("expected_cost", {}).get("prob", 1.0)
        path = plan_result.get("path", [])

        trajectory.append({
            "step": i,
            "action": action,
            "from": current,
            "to": target_id,
            "target_label": results[0].get("label", ""),
            "path": path,
            "cost": step_cost,
            "prob": step_prob,
            "traversal": plan_result.get("traversal", []),
            "high_uncertainty": plan_result.get("high_uncertainty", False),
        })

        total_cost += step_cost
        cum_prob *= step_prob
        current = target_id

    return {
        "ok": True,
        "project": project,
        "from": cursor,
        "trajectory": trajectory,
        "total_cost": total_cost,
        "cumulative_prob": round(cum_prob, 4),
        "steps": len(sequence),
    }
