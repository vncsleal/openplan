from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from openplan.core.errors import InvalidStateError, InvalidStatusError
from openplan.core.graph import _get_frontier_states, _graph_health
from openplan.core.reasoning import REASONING_FIELDS, STATUS_VALUES, ReasoningPayload
from openplan.core.state import _now, _record_event


def read_state(state_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
    node = conn.execute("SELECT * FROM nodes WHERE id = ?", (state_id,)).fetchone()
    if not node:
        raise InvalidStateError(state_id)
    node = dict(node)
    props = json.loads(node["props"]) if isinstance(node["props"], str) else node["props"]
    reasoning = ReasoningPayload.from_props(props)
    edges_out = [dict(r) for r in conn.execute(
        "SELECT e.*, n.label AS target_label FROM edges e JOIN nodes n ON n.id = e.target_id WHERE e.source_id = ?",
        (state_id,),
    ).fetchall()]
    edges_in = [dict(r) for r in conn.execute(
        "SELECT e.*, n.label AS source_label FROM edges e JOIN nodes n ON n.id = e.source_id WHERE e.target_id = ?",
        (state_id,),
    ).fetchall()]
    events = [dict(r) for r in conn.execute(
        "SELECT id, project, event_type, payload, session_id, created_at FROM events WHERE node_id = ? ORDER BY created_at DESC LIMIT 100",
        (state_id,),
    ).fetchall()]
    return {
        "ok": True,
        "state": {
            "id": node["id"],
            "label": node["label"],
            "project": node["project"],
            "status": node.get("status", "pending"),
            "activation": node["activation"],
            "frontier": bool(node["frontier"]),
            "reasoning": reasoning.to_dict(),
            "props": {k: v for k, v in props.items() if k not in REASONING_FIELDS},
            "created_at": node["created_at"],
            "updated_at": node["updated_at"],
        },
        "edges_out": edges_out,
        "edges_in": edges_in,
        "events": events,
    }


def update_state(
    state_id: str,
    conn: sqlite3.Connection,
    status: str | None = None,
    props_patch: dict[str, Any] | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    node = conn.execute("SELECT * FROM nodes WHERE id = ?", (state_id,)).fetchone()
    if not node:
        raise InvalidStateError(state_id)

    updated_fields = []

    if status is not None:
        if status not in STATUS_VALUES:
            raise InvalidStatusError(status)
        conn.execute("UPDATE nodes SET status = ?, updated_at = ? WHERE id = ?",
                     (status, _now(), state_id))
        updated_fields.append("status")

    if props_patch:
        current_props = json.loads(node["props"]) if isinstance(node["props"], str) else node["props"]
        new_props = dict(current_props)
        for k, v in props_patch.items():
            new_props[k] = v
            updated_fields.append(k)
        conn.execute("UPDATE nodes SET props = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(new_props), _now(), state_id))

    _record_event(conn, state_id, node["project"], "updated", {
        "action": "updated",
        "state_id": state_id,
        "updated_fields": updated_fields,
        "status": status,
        "props_patch": props_patch,
    }, session_id)

    return {
        "ok": True,
        "state_id": state_id,
        "updated_fields": updated_fields,
    }


def reconstruct(
    project: str,
    conn: sqlite3.Connection,
    cursor: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = config or {}

    root = conn.execute(
        "SELECT id, label FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
        (project,),
    ).fetchone()

    act_events = [dict(r) for r in conn.execute(
        "SELECT payload, created_at FROM events WHERE project = ? AND event_type = 'acted' ORDER BY created_at DESC LIMIT 10",
        (project,),
    ).fetchall()]

    recent_path = []
    for evt in reversed(act_events):
        try:
            payload = json.loads(evt["payload"]) if isinstance(evt["payload"], str) else evt["payload"]
        except (json.JSONDecodeError, TypeError):
            continue
        if "source" in payload and "target" in payload and "action" in payload:
            src_label = conn.execute("SELECT label FROM nodes WHERE id = ?", (payload["source"],)).fetchone()
            tgt_label = conn.execute("SELECT label FROM nodes WHERE id = ?", (payload["target"],)).fetchone()
            recent_path.append({
                "from": payload["source"],
                "from_label": src_label["label"] if src_label else "",
                "action": payload["action"],
                "to": payload["target"],
                "to_label": tgt_label["label"] if tgt_label else "",
                "evidence": payload.get("evidence"),
            })

    threshold = config.get("activation_threshold", 0.5)
    frontier_rows = conn.execute(
        "SELECT id, label, status, activation FROM nodes WHERE project = ? AND status IN ('pending', 'in_progress') AND activation > ? ORDER BY activation DESC",
        (project, threshold),
    ).fetchall()
    frontier = [dict(r) for r in frontier_rows]

    blocker_rows = conn.execute(
        "SELECT id, label, activation FROM nodes WHERE project = ? AND status = 'blocked' ORDER BY activation DESC",
        (project,),
    ).fetchall()
    blockers = [dict(r) for r in blocker_rows]

    open_insights = []
    for r in conn.execute(
        "SELECT e.weight_history, n.project FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ? ORDER BY e.updated_at DESC LIMIT 50",
        (project,),
    ).fetchall():
        try:
            wh = json.loads(r["weight_history"]) if isinstance(r["weight_history"], str) else (r["weight_history"] or [])
            for entry in wh:
                text = entry.get("insight", "")
                if text:
                    open_insights.append({"text": text, "applied": False})
        except (json.JSONDecodeError, TypeError):
            pass

    all_nodes = [dict(r) for r in conn.execute(
        "SELECT * FROM nodes WHERE project = ?", (project,),
    ).fetchall()]

    status_counts: dict[str, int] = defaultdict(int)
    type_counts: dict[str, int] = defaultdict(int)
    for n in all_nodes:
        status_counts[n.get("status", "pending")] += 1
        props = json.loads(n["props"]) if isinstance(n["props"], str) else n["props"]
        t = props.get("type", "unknown")
        type_counts[t] += 1

    health = _graph_health(project, conn)
    total = len(all_nodes)
    done_count = status_counts.get("done", 0)
    pct_complete = round(done_count / total * 100, 1) if total > 0 else 0.0

    stale_count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM nodes WHERE project = ? AND status = 'pending' AND "
        "datetime(updated_at) < datetime('now', '-7 days')",
        (project,),
    ).fetchone()["cnt"]

    next_target = None
    if not cursor:
        cursor = root["id"] if root else None
    if cursor:
        pending_rows = conn.execute(
            "SELECT id, label, activation FROM nodes WHERE project = ? AND status IN ('pending', 'in_progress') AND id != ? ORDER BY activation DESC LIMIT 5",
            (project, cursor),
        ).fetchall()
        if pending_rows:
            best = pending_rows[0]
            next_target = {"id": best["id"], "label": best["label"], "activation": round(best["activation"], 4)}

    return {
        "ok": True,
        "project": project,
        "cursor": cursor,
        "root": {"id": root["id"], "label": root["label"]} if root else None,
        "recent_path": recent_path,
        "frontier": frontier,
        "blockers": blockers,
        "open_insights": open_insights[:10],
        "next_target": next_target,
        "project_health": {
            "pct_complete": pct_complete,
            "total_states": total,
            "completed": done_count,
            "calibrated_count": health["calibration_count"],
            "stale_count": stale_count,
            "orphan_count": health["orphan_count"],
            "edge_count": health["edge_count"],
            "max_depth": health["max_depth"],
            "calibration_rate": round(
                health["calibration_count"] / health["edge_count"], 4
            ) if health["edge_count"] > 0 else 0.0,
        },
    }
