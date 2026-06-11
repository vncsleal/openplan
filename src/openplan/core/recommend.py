from __future__ import annotations

import json
import sqlite3
from typing import Any

from openplan.core.errors import NoPathError
from openplan.core.graph import _graph_health, _score_state
from openplan.core.planner import plan
from openplan.core.telemetry import get_telemetry


def recommend(
    project: str,
    conn: sqlite3.Connection,
    config: dict[str, Any],
    goal: str | None = None,
    max_cost: float | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    if not cursor:
        last_acted = conn.execute(
            "SELECT json_extract(payload, '$.target') AS tgt FROM events "
            "WHERE project = ? AND event_type = 'acted' ORDER BY created_at DESC LIMIT 1",
            (project,),
        ).fetchone()
        if last_acted and last_acted["tgt"]:
            cursor = last_acted["tgt"]
        else:
            root = conn.execute(
                "SELECT id FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
                (project,),
            ).fetchone()
            cursor = root["id"] if root else None

    if cursor:
        has_edges = conn.execute(
            "SELECT 1 FROM edges WHERE source_id = ? LIMIT 1", (cursor,)
        ).fetchone()
        if not has_edges:
            root = conn.execute(
                "SELECT id FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
                (project,),
            ).fetchone()
            cursor = root["id"] if root else None
            if cursor:
                root_has_edges = conn.execute(
                    "SELECT 1 FROM edges WHERE source_id = ? LIMIT 1", (cursor,)
                ).fetchone()
                if not root_has_edges:
                    return {"target": None, "reason": "project has one state with no outgoing edges", "suggested_action": {"tool": "act", "action": "implement", "target": cursor}, "state_of_project": {"total_states": 1, "completed": 0, "remaining": 1, "calibration_rate": 0.0}}

    if not cursor:
        return {"target": None, "reason": "project is empty", "state_of_project": {"total_states": 0, "completed": 0, "remaining": 0, "calibration_rate": 0.0}}

    node_rows = conn.execute(
        "SELECT id, label, activation, props FROM nodes WHERE project = ? AND id != ?",
        (project, cursor),
    ).fetchall()

    if not node_rows:
        return {"target": None, "reason": "no other states to recommend", "suggested_action": {"tool": "act", "action": "implement", "target": "create first work item"}, "state_of_project": {"total_states": 1, "completed": 0, "remaining": 1, "calibration_rate": 0.0}}

    orphan_ids = {r["id"] for r in conn.execute(
        "SELECT id FROM nodes WHERE project = ? AND NOT EXISTS "
        "(SELECT 1 FROM edges WHERE source_id = nodes.id) AND id != "
        "(SELECT MIN(n2.id) FROM nodes n2 WHERE n2.project = ?)",
        (project, project),
    ).fetchall()}

    max_visits = conn.execute(
        "SELECT MAX(json_extract(props, '$.visit_count')) AS mv FROM nodes WHERE project = ?",
        (project,),
    ).fetchone()["mv"] or 0

    threshold = config.get("activation_threshold", 0.5)
    telemetry = get_telemetry()
    conversion = telemetry.get_global_conversion_rate()
    if conversion is not None and config.get("adaptive_weights", True):
        rw = config.get("recommend_weights", {"orphan": 0.35, "visit": 0.30, "activation": 0.20, "stale": 0.15})
        rw = dict(rw)
        if conversion < 0.3:
            rw["orphan"] = min(0.50, rw.get("orphan", 0.35) + 0.10)
            rw["activation"] = max(0.10, rw.get("activation", 0.20) - 0.05)
        elif conversion > 0.6:
            rw["activation"] = min(0.35, rw.get("activation", 0.20) + 0.10)
            rw["orphan"] = max(0.15, rw.get("orphan", 0.35) - 0.05)
        config["recommend_weights"] = rw

    scored = []
    for r in node_rows:
        nid, label, activation = r["id"], r["label"], r["activation"]
        try:
            props = json.loads(r["props"])
        except (json.JSONDecodeError, TypeError):
            props = {}
        orphan = nid in orphan_ids
        score = _score_state(nid, activation, props, orphan, max_visits, threshold, config)
        scored.append((score, nid, label, activation, orphan))

    scored.sort(key=lambda x: -x[0])

    if goal and scored:
        try:
            from openplan.core.embedding import get_cache, get_provider

            if get_provider().loaded:
                cache = get_cache()
                emb_results = cache.query(goal, conn, top_k=10)
                emb_ids = {r["id"] for r in emb_results}
                boosted = [s for s in scored if s[1] in emb_ids]
                remained = [s for s in scored if s[1] not in emb_ids]
                boosted.sort(key=lambda x: -x[0])
                scored = boosted + remained
        except Exception:
            try:
                like = f"%{goal}%"
                match_ids = {r["id"] for r in conn.execute(
                    "SELECT id FROM nodes WHERE project = ? AND label LIKE ?",
                    (project, like),
                ).fetchall()}
                if match_ids:
                    matched = [s for s in scored if s[1] in match_ids]
                    unmatched = [s for s in scored if s[1] not in match_ids]
                    matched.sort(key=lambda x: -x[0])
                    scored = matched + unmatched
            except Exception:
                pass

    best_target = None
    best_plan = None
    for score, nid, label, activation, orphan in scored:
        try:
            plan_result = plan(cursor, nid, conn, config, constraints={"max_cost": max_cost} if max_cost else None)
            best_target = {"id": nid, "label": label, "activation": activation, "orphan": orphan}
            best_plan = {
                "path": plan_result["path"],
                "expected_cost": plan_result["expected_cost"],
                "traversal": plan_result["traversal"],
            }
            break
        except NoPathError:
            continue
        except Exception:
            continue

    health = _graph_health(project, conn)
    calibration_rate = health["calibration_count"] / health["edge_count"] if health["edge_count"] > 0 else 0.0
    remaining = health["state_count"] - health["calibration_count"]
    state_of_project = {
        "total_states": health["state_count"],
        "edge_count": health["edge_count"],
        "max_depth": health["max_depth"],
        "orphan_count": health["orphan_count"],
        "completed": health["calibration_count"],
        "remaining": remaining,
        "calibration_rate": round(calibration_rate, 4),
    }

    if not best_target:
        return {"target": None, "reason": "no reachable target from cursor", "state_of_project": state_of_project}

    reason_parts = []
    if best_target["orphan"]:
        reason_parts.append("orphan")
    reason_parts.append("highest-value unresolved state")
    reason = " — ".join(reason_parts)
    explanation_parts = []
    if health["orphan_count"] > 0 and best_target["orphan"]:
        explanation_parts.append(f"{health['orphan_count']} orphan states in project")
    if best_target["activation"] < threshold:
        explanation_parts.append(f"activation ({best_target['activation']:.2f}) below threshold")
    else:
        explanation_parts.append(f"activation ({best_target['activation']:.2f}) is ready")
    if health["calibration_count"] == 0 and health["edge_count"] > 0:
        explanation_parts.append("no edges calibrated yet")
    explanation = ". ".join(explanation_parts) + "."
    cost = best_plan["expected_cost"]["tokens"] if best_plan else 0

    return {
        "target": best_target["id"],
        "target_label": best_target["label"],
        "reason": reason,
        "explanation": explanation,
        "path": best_plan["traversal"] if best_plan else [],
        "cost": cost,
        "plan": best_plan,
        "state_of_project": state_of_project,
    }


def recommend_all(conn: sqlite3.Connection, config: dict[str, Any], goal: str | None = None, max_cost: float | None = None) -> list[dict[str, Any]]:
    projects = conn.execute(
        "SELECT project, MIN(id) AS root_id FROM nodes GROUP BY project ORDER BY MAX(created_at) DESC LIMIT 10"
    ).fetchall()
    results = []
    for p in projects:
        try:
            result = recommend(p["project"], conn, config, goal=goal, max_cost=max_cost, cursor=p["root_id"])
            if result.get("target"):
                results.append({"project": p["project"], **result})
        except Exception:
            continue
    results.sort(key=lambda r: r.get("cost", 0) if r.get("cost") else float("inf"))
    return results[:10]
