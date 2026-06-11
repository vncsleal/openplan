from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from openplan.core.activation import get_activation, increment_max_in_degree, mark_dirty

_path_length_cache: tuple[float, int] | None = None


def _invalidate_path_cache() -> None:
    global _path_length_cache
    _path_length_cache = None


def generate_id(project: str, conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT id FROM nodes ORDER BY id DESC LIMIT 1").fetchone()
    next_num = (int(row["id"][2:]) if row else 0) + 1
    return f"S-{next_num:06d}"


def generate_branch_id(project: str, conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(id, 3) AS INTEGER)) AS max_id FROM events WHERE event_type = 'branched'"
    ).fetchone()
    next_num = (row["max_id"] or 0) + 1
    return f"B-{next_num:06d}"


def generate_event_id(project: str, conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(id, 3) AS INTEGER)) AS max_id FROM events"
    ).fetchone()
    next_num = (row["max_id"] or 0) + 1
    return f"E-{next_num:06d}"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _idempotency_key(node_id: str, event_type: str, action: str = "") -> str:
    raw = f"{node_id}:{event_type}:{action}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _record_event(conn: sqlite3.Connection, node_id: str, project: str, event_type: str, payload: dict, session_id: str = "") -> str:
    eid = generate_event_id(project, conn)
    ikey = _idempotency_key(node_id, event_type, payload.get("action", ""))
    now = _now()
    conn.execute(
        "INSERT OR IGNORE INTO events (id, project, node_id, event_type, payload, version, idempotency_key, session_id, created_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
        (eid, project, node_id, event_type, json.dumps(payload), ikey, session_id, now),
    )
    return eid


def _ensure_node(project: str, label: str, conn: sqlite3.Connection) -> str:
    sid = generate_id(project, conn)
    now = _now()
    conn.execute(
        "INSERT INTO nodes (id, label, project, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (sid, label, project, now, now),
    )
    return sid


def _safe_savepoint(conn: sqlite3.Connection, name: str) -> bool:
    try:
        conn.execute(f"SAVEPOINT {name}")
        return True
    except sqlite3.OperationalError:
        return False


def _safe_release(conn: sqlite3.Connection, name: str, owned: bool) -> None:
    if owned:
        conn.execute(f"RELEASE SAVEPOINT {name}")


def _safe_rollback(conn: sqlite3.Connection, name: str, owned: bool) -> None:
    if owned:
        try:
            conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
        except sqlite3.OperationalError:
            pass


def _detect_cycle(conn: sqlite3.Connection, source_id: str, target_id: str, action: str) -> bool:
    row = conn.execute(
        """WITH RECURSIVE r(id) AS (
            SELECT ?
            UNION
            SELECT e.target_id FROM r
            JOIN edges e ON e.source_id = r.id
            WHERE NOT (e.source_id = ? AND e.target_id = ? AND e.action = ?)
        )
        SELECT 1 FROM r WHERE id = ? LIMIT 1""",
        (target_id, source_id, target_id, action, source_id),
    ).fetchone()
    return row is not None


def init_project(project: str, label: str | None, conn: sqlite3.Connection, session_id: str = "") -> dict[str, Any]:
    owned_init = _safe_savepoint(conn, "init_tx")
    try:
        existing = conn.execute(
            "SELECT id, label FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
            (project,),
        ).fetchone()
        if existing:
            _safe_release(conn, "init_tx", owned_init)
            return {"ok": True, "state_id": existing["id"], "label": existing["label"], "created": False}
        sid = _ensure_node(project, label or project, conn)
        now = _now()
        conn.execute("UPDATE nodes SET updated_at = ? WHERE id = ?", (now, sid))
        _record_event(conn, sid, project, "init", {"label": label or project}, session_id)
        _safe_release(conn, "init_tx", owned_init)
        return {"ok": True, "state_id": sid, "label": label or project, "created": True}
    except Exception:
        _safe_rollback(conn, "init_tx", owned_init)
        raise


def act(
    state_id: str,
    action: str,
    conn: sqlite3.Connection,
    config: dict[str, Any],
    target: str | None = None,
    evidence: str | None = None,
    thought: str | None = None,
    expected_cost: dict | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    owned = _safe_savepoint(conn, "act_tx")
    try:
        src = conn.execute("SELECT * FROM nodes WHERE id = ?", (state_id,)).fetchone()
        if not src:
            _safe_rollback(conn, "act_tx", owned)
            return {"ok": False, "error": {"code": "INVALID_STATE", "message": f"State {state_id} not found"}}
        matching = conn.execute(
            "SELECT * FROM edges WHERE source_id = ? AND action = ?", (state_id, action)
        ).fetchall()
        if not matching:
            _safe_rollback(conn, "act_tx", owned)
            return {"ok": False, "error": {"code": "INVALID_ACTION", "message": f"No edge from {state_id} with action '{action}'"}}
        if len(matching) > 1 and target:
            matching = [e for e in matching if e["target_id"] == target]
            if not matching:
                _safe_rollback(conn, "act_tx", owned)
                return {"ok": False, "error": {"code": "TARGET_NOT_FOUND", "message": f"No edge from {state_id} with action '{action}' targeting '{target}'"}}
        elif len(matching) > 1 and not target:
            matching = sorted(matching, key=lambda e: (-e["prob"], e["cost_tokens"]))
        edge = dict(matching[0])
        target_id = edge["target_id"]

        if _detect_cycle(conn, state_id, target_id, action):
            _safe_rollback(conn, "act_tx", owned)
            return {"ok": False, "error": {"code": "CYCLE_DETECTED", "message": f"Acting {state_id} -> {target_id} would create a cycle"}}

        payload = {
            "action": action, "source": state_id, "target": target_id,
            "evidence": evidence, "thought": thought, "expected_cost": expected_cost,
            "cost_actual": {"tokens": edge.get("cost_tokens", 10000), "risk": edge.get("cost_risk", 0.1)},
        }
        _record_event(conn, state_id, src["project"], "acted", payload, session_id)
        mark_dirty(state_id, conn)
        mark_dirty(target_id, conn)
        _invalidate_path_cache()
        get_activation(state_id, conn, config)
        get_activation(target_id, conn, config)

        cost_actual = {"tokens": edge.get("cost_tokens", 10000), "risk": edge.get("cost_risk", 0.1)}
        cost_delta = None
        if expected_cost is not None:
            cost_delta = {
                "tokens": cost_actual["tokens"] - expected_cost.get("tokens", 0),
                "risk": cost_actual["risk"] - expected_cost.get("risk", 0.0),
            }

        from openplan.core.graph import _get_frontier_states

        frontier = _get_frontier_states(src["project"], conn, config)
        _safe_release(conn, "act_tx", owned)
    except Exception:
        _safe_rollback(conn, "act_tx", owned)
        raise

    return {
        "ok": True, "next_state": target_id, "cursor": target_id,
        "activation_delta": {state_id: get_activation(state_id, conn, config), target_id: get_activation(target_id, conn, config)},
        "cost_actual": cost_actual, "cost_delta": cost_delta,
        "new_frontier": [s["id"] for s in frontier],
    }


def branch(
    state_id: str,
    options: list[dict],
    conn: sqlite3.Connection,
    config: dict[str, Any],
    session_id: str = "",
) -> dict[str, Any]:
    src = conn.execute("SELECT * FROM nodes WHERE id = ?", (state_id,)).fetchone()
    if not src:
        return {"ok": False, "error": {"code": "INVALID_STATE", "message": f"State {state_id} not found"}}
    if not options:
        return {"ok": False, "error": {"code": "NO_OPTIONS", "message": "At least one option required"}}

    project = src["project"]
    now = _now()
    owned_branch = _safe_savepoint(conn, "branch_tx")
    try:
        branch_id = generate_branch_id(project, conn)
        states_created = []
        for opt in options:
            sid = generate_id(project, conn)
            label = opt.get("label", "")
            conn.execute(
                "INSERT INTO nodes (id, label, project, props, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (sid, label, project, json.dumps({"boost": True, "boosted_at": now}), now, now),
            )
            action = opt["action"]
            cost_tokens = opt.get("expected_cost", {}).get("tokens", 10000)
            cost_risk = opt.get("expected_cost", {}).get("risk", 0.1)
            prob = opt.get("prob", 0.8)
            conn.execute(
                "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (state_id, sid, action, cost_tokens, cost_risk, prob, now, now),
            )
            states_created.append(sid)

        _record_event(conn, state_id, project, "branched", {
            "action": "branched", "source": state_id, "branch_id": branch_id,
            "options": len(options), "states_created": states_created,
        }, session_id)
        _safe_release(conn, "branch_tx", owned_branch)
    except Exception:
        _safe_rollback(conn, "branch_tx", owned_branch)
        raise

    mark_dirty(state_id, conn)
    _invalidate_path_cache()
    for s in states_created:
        increment_max_in_degree(s, conn)

    return {"ok": True, "branch_id": branch_id, "options": len(options), "states_created": states_created}
