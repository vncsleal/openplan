import sqlite3
import json
import uuid
from datetime import datetime, timezone

from openplan.db.schema import tokenize
from openplan.core.costs import estimate_cost

DEFAULT_PHASE_TEMPLATES = [
    ("Scaffold + project structure", "implement"),
    ("Core feature implementation", "implement"),
    ("Integration and testing", "implement"),
    ("Deploy to production", "deploy"),
]


def plan_project(
    conn: sqlite3.Connection,
    goal: str,
    context: str = "",
    replan: bool = False,
    api_key: str | None = None,
) -> dict:
    goal_tokens = tokenize(goal)
    context_tokens = tokenize(context)

    # If replan, archive the current active route
    active = None
    if replan:
        active = conn.execute(
            "SELECT id, goal, context FROM routes WHERE status = 'active' AND archived = 0 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

    # Generate phases
    phases = _generate_phases(conn, goal_tokens, context_tokens, api_key)
    total_cost = sum(p["expected_cost"] for p in phases)

    # Create route
    route_id = f"R-{uuid.uuid4().hex[:6].upper()}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%fZ")

    conn.execute(
        """INSERT INTO routes (id, project, goal, context, total_expected, status,
           goal_tokens, context_tokens, created_at)
           VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
        (route_id, _infer_project(conn), goal, context, total_cost, goal_tokens, context_tokens, now),
    )

    for i, p in enumerate(phases):
        phase_id = f"P-{uuid.uuid4().hex[:8].upper()}"
        label_tokens = tokenize(p["label"])
        conn.execute(
            """INSERT INTO route_phases (id, route_id, label, action, expected_cost, status, sequence, label_tokens, created_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (phase_id, route_id, p["label"], p["action"], p["expected_cost"], i, label_tokens, now),
        )

    # Archive old route if replanning
    archived_info = []
    if active:
        conn.execute(
            "UPDATE routes SET archived = 1, status = 'archived' WHERE id = ?",
            (active["id"],),
        )
        archived_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM route_phases WHERE route_id = ? AND status = 'done'",
            (active["id"],),
        ).fetchone()["cnt"]
        archived_info.append({
            "id": active["id"],
            "phases_completed": archived_count,
            "abandon_reason": "replanned via plan(replan=true)",
        })

    conn.commit()

    return {
        "route_id": route_id,
        "phases": phases,
        "total_cost": total_cost,
        "archived_routes": archived_info,
    }


def _generate_phases(
    conn: sqlite3.Connection,
    goal_tokens: str,
    context_tokens: str,
    api_key: str | None = None,
) -> list[dict]:
    """Generate phases from completed_sequences matching, or use defaults."""
    # Try to find a matching sequence from completed_sequences
    sequences = _find_matching_sequences(conn, goal_tokens, context_tokens)
    if sequences:
        best = sequences[0]
        actions = best["action_sequence"].split(",")
    else:
        actions = [t[1] for t in DEFAULT_PHASE_TEMPLATES]
        default_labels = [t[0] for t in DEFAULT_PHASE_TEMPLATES]

    phases = []
    if actions:
        default_labels_list = _LABEL_TEMPLATES.get(len(actions), [])
        for i, action in enumerate(actions):
            label = default_labels_list[i][0] if i < len(default_labels_list) else f"Phase {i+1}"
            exp, clo, chi, level, samples = estimate_cost(conn, action.strip(), goal_tokens, label, api_key)
            phases.append({
                "label": label,
                "action": action.strip(),
                "expected_cost": exp,
                "ci": [clo, chi],
                "match_level": level,
                "match_samples": samples,
            })
    else:
        for i, (label, action) in enumerate(DEFAULT_PHASE_TEMPLATES):
            exp, clo, chi, level, samples = estimate_cost(conn, action, goal_tokens, label, api_key)
            phases.append({
                "label": label,
                "action": action,
                "expected_cost": exp,
                "ci": [clo, chi],
                "match_level": level,
                "match_samples": samples,
            })

    return phases


_LABEL_TEMPLATES: dict[int, list[tuple[str, str]]] = {
    2: [("Phase 1 — Setup", "implement"), ("Phase 2 — Deliver", "deploy")],
    3: [("Scaffold + setup", "implement"), ("Core logic", "implement"), ("Deploy", "deploy")],
    4: [("Scaffold + setup", "implement"), ("Core implementation", "implement"), ("Integration + test", "test"), ("Deploy", "deploy")],
}


def _find_matching_sequences(conn: sqlite3.Connection, goal_tokens: str, context_tokens: str) -> list[dict]:
    """Find completed sequences matching goal or context keywords."""
    if not goal_tokens and not context_tokens:
        return []

    tokens = (goal_tokens + " " + context_tokens).split()
    if not tokens:
        return []

    results = []
    seen = set()
    for token in tokens[:5]:  # Limit to top 5 tokens
        for row in conn.execute(
            """SELECT action_sequence, AVG(efficiency) as eff, COUNT(*) as cnt
               FROM completed_sequences
               WHERE goal_tokens LIKE ? OR context_tokens LIKE ?
               GROUP BY action_sequence
               HAVING cnt >= 1
               ORDER BY eff ASC LIMIT 3""",
            (f"%{token}%", f"%{token}%"),
        ).fetchall():
            key = row["action_sequence"]
            if key not in seen:
                seen.add(key)
                results.append({
                    "action_sequence": key,
                    "avg_efficiency": row["eff"],
                    "samples": row["cnt"],
                })

    results.sort(key=lambda r: r["avg_efficiency"])
    return results


def _infer_project(conn: sqlite3.Connection) -> str:
    """Infer project name from .openplan file or env, or use a default."""
    import os
    cwd = os.getcwd()
    openplan_path = os.path.join(cwd, ".openplan")
    if os.path.exists(openplan_path):
        try:
            with open(openplan_path) as f:
                data = json.load(f)
                return data.get("project", os.path.basename(cwd))
        except (json.JSONDecodeError, OSError):
            pass
    return os.path.basename(cwd)
