import sqlite3
from datetime import datetime, timezone

from openplan.core.costs import estimate_cost


def checkpoint_phase(
    conn: sqlite3.Connection,
    route_id: str,
    phase_label: str,
    actual_cost: int,
    api_key: str | None = None,
) -> dict:
    # Find the phase by label matching
    phase = conn.execute(
        """SELECT rp.*, r.goal_tokens
           FROM route_phases rp
           JOIN routes r ON r.id = rp.route_id
           WHERE rp.route_id = ? AND rp.status = 'pending'
           ORDER BY rp.sequence ASC""",
        (route_id,),
    ).fetchall()

    matched = None
    for p in phase:
        if p["label"] == phase_label or phase_label.startswith(p["label"].split("(")[0].strip()):
            matched = p
            break

    if not matched:
        # Try to match by checking if phase_label subsumes pending phases
        total_expected = 0
        for p in phase:
            if p["label"].lower() in phase_label.lower() or all(w in phase_label.lower() for w in p["label"].lower().split()):
                total_expected += p["expected_cost"]
        if total_expected > 0:
            matched = phase[0]
            matched_expected = total_expected
        else:
            return {"error": True, "message": f"No pending phase matching '{phase_label}' in route {route_id}"}
    else:
        matched_expected = matched["expected_cost"]

    # Mark phase as done
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%fZ")
    outcome = _derive_outcome(matched_expected, actual_cost)

    conn.execute(
        """UPDATE route_phases SET status = 'done', actual_cost = ?, outcome = ?, updated_at = ?
           WHERE id = ?""",
        (actual_cost, outcome, now, matched["id"]),
    )

    # Update route total_actual
    conn.execute(
        "UPDATE routes SET total_actual = COALESCE(total_actual, 0) + ? WHERE id = ?",
        (actual_cost, route_id),
    )

    # Find next pending phase
    next_phase = conn.execute(
        """SELECT label, expected_cost FROM route_phases
           WHERE route_id = ? AND status = 'pending'
           ORDER BY sequence ASC LIMIT 1""",
        (route_id,),
    ).fetchone()

    # Check if route is completed
    remaining = conn.execute(
        "SELECT COUNT(*) as cnt FROM route_phases WHERE route_id = ? AND status != 'done'",
        (route_id,),
    ).fetchone()["cnt"]

    route_completed = remaining == 0
    if route_completed:
        conn.execute(
            "UPDATE routes SET status = 'completed', completed_at = ? WHERE id = ?",
            (now, route_id),
        )

    conn.commit()

    deviation_ratio = round(actual_cost / matched_expected, 2) if matched_expected > 0 else 1.0
    deviation_level = "low" if deviation_ratio <= 1.3 else ("medium" if deviation_ratio <= 2.0 else "high")

    # Compute hazards for next phase
    hazards = []
    if next_phase:
        phase_row = conn.execute(
            "SELECT * FROM route_phases WHERE route_id = ? AND status = 'pending' ORDER BY sequence ASC LIMIT 1",
            (route_id,),
        ).fetchone()
        if phase_row:
            route_row = conn.execute(
                "SELECT goal_tokens, context_tokens FROM routes WHERE id = ?", (route_id,)
            ).fetchone()
            goal_tokens = (route_row["goal_tokens"] + " " + route_row["context_tokens"]) if route_row else ""
            _, clo, chi, _, _ = estimate_cost(conn, "implement", goal_tokens, phase_row["label"])
            if chi > 0 and clo > 0 and chi / clo > 3.0:
                hazards.append({
                    "type": "high_variance",
                    "detail": f"{phase_row['label']} CI [{clo:.0f}-{chi:.0f}] — wide spread",
                    "suggested_buffer": round(chi / clo, 1),
                })

    return {
        "phase_completed": matched["label"] if matched else phase_label,
        "actual_cost": actual_cost,
        "expected_cost": matched_expected,
        "deviation": {"ratio": deviation_ratio, "level": deviation_level, "outcome": outcome},
        "next_phase": {"label": next_phase["label"], "expected_cost": next_phase["expected_cost"]} if next_phase else None,
        "hazards": hazards,
        "route_completed": route_completed,
    }


def get_route_status(
    conn: sqlite3.Connection,
    route_id: str | None = None,
    project: str | None = None,
) -> dict:
    if project and not route_id:
        row = conn.execute(
            "SELECT id FROM routes WHERE project = ? AND archived = 0 ORDER BY created_at DESC LIMIT 1",
            (project,),
        ).fetchone()
        if row:
            route_id = row["id"]

    if not route_id:
        return {"error": True, "message": "No route_id or project provided"}

    route = conn.execute(
        "SELECT * FROM routes WHERE id = ?", (route_id,)
    ).fetchone()
    if not route:
        return {"error": True, "message": f"Route {route_id} not found"}

    phases = conn.execute(
        """SELECT label, status, expected_cost, actual_cost, outcome
           FROM route_phases WHERE route_id = ?
           ORDER BY sequence ASC""",
        (route_id,),
    ).fetchall()

    total = len(phases)
    done = sum(1 for p in phases if p["status"] == "done")
    pending_idx = None
    for i, p in enumerate(phases):
        if p["status"] == "pending":
            pending_idx = i
            break

    phase_list = []
    for p in phases:
        phase_list.append({
            "label": p["label"],
            "status": p["status"],
            "expected": p["expected_cost"],
            "actual": p["actual_cost"],
        })

    return {
        "route_id": route_id,
        "project": route["project"],
        "goal": route["goal"],
        "status": route["status"],
        "phases": phase_list,
        "position": f"phase {done + 1}/{total}" if pending_idx is not None else "completed",
    }


def _derive_outcome(expected: float, actual: float) -> str:
    ratio = actual / expected if expected > 0 else 1.0
    if ratio <= 1.3:
        return "success"
    elif ratio <= 2.0:
        return "partial"
    return "failure"
