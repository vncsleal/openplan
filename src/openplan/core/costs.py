from __future__ import annotations

import json
import sqlite3
from typing import Any

from openplan.core.utils import now as _now

_ACTION_COST_DEFAULTS: dict[str, float] = {
    "research": 1000, "explore": 1000, "analyze": 1000, "investigate": 1000,
    "design": 2000, "plan": 2000, "architect": 2000,
    "test": 1500, "verify": 1500, "validate": 1500, "check": 1500,
    "document": 500, "write_docs": 500, "readme": 500,
    "implement": 5000, "build": 5000, "create": 5000, "write": 5000, "add": 5000,
    "deploy": 3000, "release": 3000, "ship": 3000, "publish": 3000,
}


def _get_default_cost(action: str, project_type: str, conn: sqlite3.Connection) -> float:
    if project_type:
        bl = conn.execute(
            "SELECT cost_tokens FROM cost_baselines WHERE project IS NULL AND project_type = ? AND action = ?",
            (project_type, action),
        ).fetchone()
        if bl:
            return bl["cost_tokens"]
    return _ACTION_COST_DEFAULTS.get(action, 5000)


def _upsert_baseline(conn: sqlite3.Connection, project: str | None, project_type: str, action: str, cost_tokens: float, cost_risk: float) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO cost_baselines (project, project_type, action, cost_tokens, cost_risk, sample_count, updated_at) "
        "VALUES (?, ?, ?, ?, ?, COALESCE((SELECT sample_count + 1 FROM cost_baselines WHERE project IS ? AND project_type = ? AND action = ?), 1), ?)",
        (project, project_type, action, cost_tokens, cost_risk, project, project_type, action, _now()),
    )


def _update_cost_baseline(project: str, project_type: str, action: str, cost_tokens: float, cost_risk: float, conn: sqlite3.Connection) -> None:
    _upsert_baseline(conn, project, project_type, action, cost_tokens, cost_risk)
    if project_type:
        _upsert_baseline(conn, None, project_type, action, cost_tokens, cost_risk)


def _auto_calibrate_edge(conn: sqlite3.Connection, edge: dict, target_id: str, outcome: str = "success", actual_cost: float | None = None, source: str = "auto") -> None:
    wh = json.loads(edge.get("weight_history", "[]"))
    has_real_data = any(w.get("source") != "auto" for w in wh)
    if has_real_data:
        return
    wh.append({"actual_cost": {"tokens": actual_cost}, "expected_cost": {"tokens": edge.get("cost_tokens")}, "outcome": outcome, "source": source, "learned_at": _now()})
    conn.execute(
        "UPDATE edges SET weight_history = ?, updated_at = ? WHERE source_id = ? AND target_id = ? AND action = ?",
        (json.dumps(wh), _now(), edge["source_id"], target_id, edge.get("action", "")),
    )


def _chain_calibrate(conn: sqlite3.Connection, project: str, current_event_id: str) -> None:
    row = conn.execute(
        "SELECT payload FROM events WHERE id = ? AND event_type = 'acted'",
        (current_event_id,),
    ).fetchone()
    if not row:
        return
    payload = json.loads(row["payload"])
    if not payload.get("cost_actual"):
        return
    source_id = payload.get("source")
    if not source_id:
        return
    prev_act = conn.execute(
        "SELECT payload FROM events WHERE project = ? AND event_type = 'acted' AND id < ? AND node_id = ? ORDER BY created_at DESC LIMIT 1",
        (project, current_event_id, source_id),
    ).fetchone()
    if not prev_act:
        return
    prev_payload = json.loads(prev_act["payload"])
    prev_actual = prev_payload.get("cost_actual", {}).get("tokens")
    prev_action = prev_payload.get("action")
    if not prev_actual or not prev_action:
        return
    prev_edge = conn.execute(
        "SELECT * FROM edges WHERE source_id = ? AND target_id = ? AND action = ?",
        (prev_payload.get("source"), source_id, prev_action),
    ).fetchone()
    if prev_edge:
        wh = json.loads(prev_edge["weight_history"])
        has_real = any(w.get("source") != "auto" for w in wh)
        if not has_real and prev_payload.get("expected_cost", {}).get("tokens"):
            wh.append({"actual_cost": {"tokens": prev_actual}, "expected_cost": {"tokens": prev_payload["expected_cost"]["tokens"]}, "outcome": "success", "source": "auto", "learned_at": _now()})
            conn.execute(
                "UPDATE edges SET weight_history = ?, updated_at = ? WHERE source_id = ? AND target_id = ? AND action = ?",
                (json.dumps(wh), _now(), prev_edge["source_id"], source_id, prev_action),
            )
