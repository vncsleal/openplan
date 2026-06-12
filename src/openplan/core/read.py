from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from openplan.core.errors import InvalidStateError, InvalidStatusError
from openplan.core.graph import _graph_health
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


def reconstruct(project: str, conn: sqlite3.Connection) -> dict[str, Any]:
    nodes = [dict(r) for r in conn.execute(
        "SELECT * FROM nodes WHERE project = ? ORDER BY created_at ASC",
        (project,),
    ).fetchall()]

    states = []
    for n in nodes:
        props = json.loads(n["props"]) if isinstance(n["props"], str) else n["props"]
        reasoning = ReasoningPayload.from_props(props)
        states.append({
            "id": n["id"],
            "label": n["label"],
            "status": n.get("status", "pending"),
            "activation": round(n["activation"], 4),
            "frontier": bool(n["frontier"]),
            "reasoning": reasoning.to_dict(),
            "raw_props": {k: v for k, v in props.items() if k not in REASONING_FIELDS},
            "created_at": n["created_at"],
            "updated_at": n["updated_at"],
        })

    edges = [dict(r) for r in conn.execute(
        "SELECT e.*, src.label AS source_label, tgt.label AS target_label "
        "FROM edges e "
        "JOIN nodes src ON src.id = e.source_id "
        "JOIN nodes tgt ON tgt.id = e.target_id "
        "WHERE src.project = ?",
        (project,),
    ).fetchall()]

    status_counts: dict[str, int] = defaultdict(int)
    type_counts: dict[str, int] = defaultdict(int)
    for n in nodes:
        status_counts[n.get("status", "pending")] += 1
        props = json.loads(n["props"]) if isinstance(n["props"], str) else n["props"]
        t = props.get("type", "unknown")
        type_counts[t] += 1

    health = _graph_health(project, conn)

    return {
        "ok": True,
        "project": project,
        "states": states,
        "edges": edges,
        "statistics": {
            "total_states": len(states),
            "total_edges": len(edges),
            "status_counts": dict(status_counts),
            "type_counts": dict(type_counts),
            "orphan_count": health["orphan_count"],
            "max_depth": health["max_depth"],
            "calibration_rate": round(
                health["calibration_count"] / health["edge_count"], 4
            ) if health["edge_count"] > 0 else 0.0,
        },
    }
