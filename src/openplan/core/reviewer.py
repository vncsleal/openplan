import sqlite3
from datetime import datetime, timezone

from openplan.core.costs import compute_personal_bias, _derive_outcome


def review_route(
    conn: sqlite3.Connection,
    route_id: str | None = None,
    project: str | None = None,
    api_key: str | None = None,
) -> dict:
    if project and not route_id:
        row = conn.execute(
            "SELECT id FROM routes WHERE project = ? AND archived = 0 ORDER BY created_at DESC LIMIT 1",
            (project,),
        ).fetchone()
        if row:
            route_id = row["id"]

    if not route_id:
        row = conn.execute(
            "SELECT id FROM routes WHERE status = 'completed' ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
        if row:
            route_id = row["id"]

    if not route_id:
        return {"error": True, "message": "No route to review"}

    route = conn.execute("SELECT * FROM routes WHERE id = ?", (route_id,)).fetchone()
    if not route:
        return {"error": True, "message": f"Route {route_id} not found"}

    phases = conn.execute(
        """SELECT * FROM route_phases WHERE route_id = ? ORDER BY sequence ASC""",
        (route_id,),
    ).fetchall()

    total_expected = sum(p["expected_cost"] for p in phases)
    total_actual = sum(p["actual_cost"] for p in phases if p["actual_cost"])
    phases_completed = sum(1 for p in phases if p["status"] == "done")
    accuracy = round(min(total_actual / total_expected, total_expected / total_actual), 2) if total_expected > 0 else 0

    deviations = []
    accuracy_by_action: dict[str, dict] = {}
    for p in phases:
        if p["actual_cost"]:
            dev_ratio = round(p["actual_cost"] / p["expected_cost"], 2) if p["expected_cost"] > 0 else 0
            direction = "over" if dev_ratio > 1.0 else "under"
            deviations.append({
                "phase": p["label"],
                "expected": p["expected_cost"],
                "actual": p["actual_cost"],
                "ratio": dev_ratio,
            })
            acc = accuracy_by_action.setdefault(p["action"], {"count": 0, "total_ratio": 0.0})
            acc["count"] += 1
            acc["total_ratio"] += dev_ratio

    for k, v in accuracy_by_action.items():
        v["avg_deviation"] = round(v["total_ratio"] / v["count"], 2)
        del v["total_ratio"]

    # Self-diagnostics
    archived_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM routes WHERE archived = 1 AND project = ?",
        (route["project"],),
    ).fetchone()["cnt"]

    all_routes_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM routes WHERE project = ?",
        (route["project"],),
    ).fetchone()["cnt"]

    hazards_fired = 0
    hazards_relevant = 0
    for p in phases:
        if p["actual_cost"] and p["expected_cost"]:
            ratio = p["actual_cost"] / p["expected_cost"]
            if ratio > 1.2:
                hazards_fired += 1
                hazards_relevant += 1

    self_diagnostics = {
        "routes_created": all_routes_count,
        "routes_archived": archived_count,
        "phases_planned": len(phases),
        "phases_completed": phases_completed,
        "hazards_fired": hazards_fired,
        "hazard_precision": round(hazards_relevant / hazards_fired, 2) if hazards_fired > 0 else None,
        "tool_calls": 0,
    }

    # Path learning
    path_learning = []
    action_seq = ",".join(p["action"] for p in phases if p["actual_cost"])
    if action_seq:
        existing = conn.execute(
            "SELECT COUNT(*) as cnt FROM completed_sequences WHERE goal_tokens = ? AND action_sequence = ?",
            (route["goal_tokens"], action_seq),
        ).fetchone()["cnt"]
        if existing == 0:
            conn.execute(
                """INSERT INTO completed_sequences
                   (id, goal_tokens, context_tokens, action_sequence, total_expected, total_actual,
                    efficiency, outcome, session_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"seq_{datetime.now(timezone.utc).timestamp()}",
                    route["goal_tokens"],
                    route["context_tokens"],
                    action_seq,
                    total_expected,
                    total_actual,
                    round(min(total_actual / total_expected, 1.0), 4) if total_expected > 0 else 0,
                    "success" if accuracy >= 0.8 else "partial",
                    "",
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%fZ"),
                ),
            )
            conn.commit()
            path_learning.append({
                "observation": f"stored sequence {action_seq} for path learning",
                "action_sequence": action_seq,
            })

    # Mesh status
    pending_sync = conn.execute(
        "SELECT COUNT(*) as cnt FROM calibration_events WHERE synced = 0"
    ).fetchone()["cnt"]

    result = {
        "summary": {
            "estimated": total_expected,
            "actual": total_actual,
            "phases_completed": phases_completed,
            "accuracy": accuracy,
        },
        "deviations": deviations[:10],  # Limit to 10 phases
        "accuracy_by_action": accuracy_by_action,
        "cost_learning": [],
        "path_learning": path_learning[:5],
        "self_diagnostics": self_diagnostics,
        "mesh": {"shared": pending_sync},
    }

    # Personal bias info
    if api_key:
        bias = compute_personal_bias(conn, api_key)
        result["personal_bias"] = {"ratio": bias, "based_on": phases_completed}

    return result
