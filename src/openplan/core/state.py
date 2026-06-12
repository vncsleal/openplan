from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger("openplan.state")

from openplan.core.activation import get_activation, increment_max_in_degree, mark_dirty
from openplan.core.errors import (
    CycleDetectedError, InvalidActionError, InvalidStateError,
    NoOptionsError, OpenPlanError, PreconditionError, TerminalStateError,
)
from openplan.core.graph import _get_frontier_states, _invalidate_graph_cache
from openplan.core.reasoning import ReasoningPayload


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


def _ensure_node(project: str, label: str, conn: sqlite3.Connection, parent_id: str | None = None) -> str:
    sid = generate_id(project, conn)
    now = _now()
    conn.execute(
        "INSERT INTO nodes (id, label, project, parent_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (sid, label, project, parent_id, now, now),
    )
    return sid


def _safe_savepoint(conn: sqlite3.Connection, name: str) -> bool:
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
        _log.warning("Invalid savepoint name: %s", name)
        return False
    try:
        conn.execute(f'SAVEPOINT "{name}"')
        return True
    except sqlite3.OperationalError as e:
        _log.warning("Savepoint %s failed: %s", name, e)
        return False


def _safe_release(conn: sqlite3.Connection, name: str, owned: bool) -> None:
    if owned:
        conn.execute(f'RELEASE SAVEPOINT "{name}"')


def _safe_rollback(conn: sqlite3.Connection, name: str, owned: bool) -> None:
    if owned:
        try:
            conn.execute(f'ROLLBACK TO SAVEPOINT "{name}"')
        except sqlite3.OperationalError:
            pass


def _increment_visit(state_id: str, conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE nodes SET props = json_set(props, '$.visit_count', "
        "COALESCE(json_extract(props, '$.visit_count'), 0) + 1) WHERE id = ?",
        (state_id,),
    )


def _auto_calibrate(conn: sqlite3.Connection, edge: dict, target_id: str) -> None:
    wh_raw = edge.get("weight_history") or "[]"
    try:
        wh = json.loads(wh_raw) if isinstance(wh_raw, str) else (wh_raw or [])
    except (json.JSONDecodeError, TypeError):
        wh = []
    now = _now()
    wh.append({
        "actual_cost": {"tokens": edge.get("cost_tokens", 10000)},
        "expected_cost": {"tokens": edge.get("cost_tokens", 10000)},
        "learned_at": now,
        "auto": True,
    })
    conn.execute(
        "UPDATE edges SET weight_history = ?, updated_at = ? WHERE source_id = ? AND target_id = ? AND action = ?",
        (json.dumps(wh), now, edge["source_id"], target_id, edge["action"]),
    )


def _prune_stale_branches(source_id: str, conn: sqlite3.Connection, session_id: str = "", rate_limit: int = 5, stale_hours: float = 24.0) -> None:
    cutoff = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - stale_hours * 3600, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    candidates = conn.execute(
        "SELECT e.target_id, n.label FROM edges e "
        "JOIN nodes n ON n.id = e.target_id "
        "WHERE e.source_id = ? AND n.created_at < ? "
        "AND NOT EXISTS (SELECT 1 FROM events ev WHERE ev.node_id = n.id AND ev.event_type IN ('acted', 'branched')) "
        "AND NOT EXISTS (SELECT 1 FROM edges e2 WHERE e2.source_id = n.id) "
        "LIMIT ?",
        (source_id, cutoff, rate_limit),
    ).fetchall()
    for row in candidates:
        tid = row["target_id"]
        _log.warning("Pruning stale branch %s (%s) from source %s", tid, row["label"], source_id)
        conn.execute("DELETE FROM events WHERE node_id = ?", (tid,))
        conn.execute("DELETE FROM edges WHERE source_id = ? AND target_id = ?", (source_id, tid))
        conn.execute("DELETE FROM edges WHERE source_id = ?", (tid,))
        conn.execute("DELETE FROM nodes WHERE id = ?", (tid,))


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


def init_project(project: str, label: str | None, conn: sqlite3.Connection, session_id: str = "", project_type: str = "", goal: str = "") -> dict[str, Any]:
    owned_init = _safe_savepoint(conn, "init_tx")
    try:
        existing = conn.execute(
            "SELECT id, label FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
            (project,),
        ).fetchone()
        if existing:
            if goal:
                conn.execute("UPDATE nodes SET goal = ?, updated_at = ? WHERE id = ?", (goal, _now(), existing["id"]))
                _record_event(conn, existing["id"], project, "goal_set", {"goal": goal}, session_id)
            if project_type:
                conn.execute("UPDATE nodes SET project_type = ?, updated_at = ? WHERE id = ?", (project_type, _now(), existing["id"]))
            _safe_release(conn, "init_tx", owned_init)
            return {"ok": True, "state_id": existing["id"], "label": existing["label"], "created": False}
        sid = _ensure_node(project, label or project, conn)
        now = _now()
        if project_type:
            conn.execute("UPDATE nodes SET project_type = ?, updated_at = ? WHERE id = ?", (project_type, now, sid))
        if goal:
            conn.execute("UPDATE nodes SET goal = ?, updated_at = ? WHERE id = ?", (goal, now, sid))
            _record_event(conn, sid, project, "goal_set", {"goal": goal}, session_id)
        conn.execute("UPDATE nodes SET updated_at = ? WHERE id = ?", (now, sid))
        _record_event(conn, sid, project, "init", {"label": label or project, "project_type": project_type, "goal": goal}, session_id)
        _safe_release(conn, "init_tx", owned_init)
        return {"ok": True, "state_id": sid, "label": label or project, "project_type": project_type, "goal": goal, "created": True}
    except OpenPlanError:
        _safe_rollback(conn, "init_tx", owned_init)
        raise
    except Exception:
        _safe_rollback(conn, "init_tx", owned_init)
        raise


def _check_preconditions(edge: dict, conn: sqlite3.Connection) -> None:
    raw = edge.get("conditions", "")
    if not raw:
        return
    try:
        conditions = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(conditions, list):
        return
    for cond in conditions:
        if isinstance(cond, dict):
            field = cond.get("field", "")
            expected = cond.get("value")
            source_id = edge["source_id"]
            row = conn.execute("SELECT props FROM nodes WHERE id = ?", (source_id,)).fetchone()
            if row:
                try:
                    props = json.loads(row["props"]) if isinstance(row["props"], str) else row["props"]
                except (json.JSONDecodeError, TypeError):
                    props = {}
                actual = props.get(field)
                if actual != expected:
                    raise PreconditionError(source_id, edge.get("action", ""), f"{field}={expected}")


def _update_cost_baseline(project_type: str, action: str, cost_tokens: float, cost_risk: float, conn: sqlite3.Connection) -> None:
    if not project_type:
        return
    existing = conn.execute(
        "SELECT cost_tokens, cost_risk, sample_count FROM cost_baselines WHERE project_type = ? AND action = ?",
        (project_type, action),
    ).fetchone()
    if existing:
        n = existing["sample_count"] + 1
        avg_tokens = (existing["cost_tokens"] * existing["sample_count"] + cost_tokens) / n
        avg_risk = (existing["cost_risk"] * existing["sample_count"] + cost_risk) / n
        conn.execute(
            "UPDATE cost_baselines SET cost_tokens = ?, cost_risk = ?, sample_count = ?, updated_at = ? WHERE project_type = ? AND action = ?",
            (round(avg_tokens, 2), round(avg_risk, 4), n, _now(), project_type, action),
        )
    else:
        conn.execute(
            "INSERT INTO cost_baselines (project_type, action, cost_tokens, cost_risk, sample_count, updated_at) VALUES (?, ?, ?, ?, 1, ?)",
            (project_type, action, cost_tokens, cost_risk, _now()),
        )


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
    reasoning: dict | None = None,
    postconditions: dict | None = None,
) -> dict[str, Any]:
    owned = _safe_savepoint(conn, "act_tx")
    try:
        raw_src = conn.execute("SELECT * FROM nodes WHERE id = ?", (state_id,)).fetchone()
        if not raw_src:
            raise InvalidStateError(state_id)
        src = dict(raw_src)
        if src.get("terminal"):
            raise TerminalStateError(state_id)
        target_id = target
        if target:
            tgt = conn.execute("SELECT id FROM nodes WHERE id = ?", (target,)).fetchone()
            if not tgt:
                target_id = _ensure_node(src["project"], target, conn, parent_id=state_id if state_id != src["id"] else None)
            conn.execute(
                "INSERT OR IGNORE INTO edges (source_id, target_id, action, prob, created_at, updated_at) VALUES (?, ?, ?, 0.8, ?, ?)",
                (state_id, target_id, action, _now(), _now()),
            )
            if reasoning:
                reasoning_payload = ReasoningPayload.from_props(reasoning)
                reasoning_payload.validate()
                existing_props = json.loads(
                    conn.execute("SELECT props FROM nodes WHERE id = ?", (target_id,)).fetchone()["props"]
                )
                merged = reasoning_payload.merge_into_props(existing_props)
                conn.execute("UPDATE nodes SET props = ?, updated_at = ? WHERE id = ?",
                             (json.dumps(merged), _now(), target_id))
        else:
            matching = conn.execute(
                "SELECT * FROM edges WHERE source_id = ? AND action = ?", (state_id, action)
            ).fetchall()
            if not matching:
                raise InvalidActionError(state_id, action)
            if len(matching) > 1:
                matching = sorted(matching, key=lambda e: (-e["prob"], e["cost_tokens"]))
            target_id = dict(matching[0])["target_id"]

        edge = conn.execute(
            "SELECT * FROM edges WHERE source_id = ? AND target_id = ? AND action = ?",
            (state_id, target_id, action),
        ).fetchone()
        if not edge:
            raise InvalidActionError(state_id, action)
        edge = dict(edge)

        _check_preconditions(edge, conn)

        if _detect_cycle(conn, state_id, target_id, action):
            raise CycleDetectedError(state_id, target_id)

        payload = {
            "action": action, "source": state_id, "target": target_id,
            "evidence": evidence, "thought": thought, "expected_cost": expected_cost,
            "cost_actual": {"tokens": edge.get("cost_tokens", 10000), "risk": edge.get("cost_risk", 0.1)},
        }
        _record_event(conn, state_id, src["project"], "acted", payload, session_id)
        _increment_visit(target_id, conn)
        _auto_calibrate(conn, edge, target_id)
        if postconditions:
            current_props = json.loads(
                conn.execute("SELECT props FROM nodes WHERE id = ?", (target_id,)).fetchone()["props"]
            ) if conn.execute("SELECT props FROM nodes WHERE id = ?", (target_id,)).fetchone() else {}
            merged = dict(current_props)
            merged.update(postconditions)
            conn.execute("UPDATE nodes SET props = ?, updated_at = ? WHERE id = ?",
                         (json.dumps(merged), _now(), target_id))
        cost_actual = {"tokens": edge.get("cost_tokens", 10000), "risk": edge.get("cost_risk", 0.1)}
        project_type = src.get("project_type", "") or ""
        if project_type:
            _update_cost_baseline(project_type, action, cost_actual["tokens"], cost_actual["risk"], conn)
        _prune_stale_branches(state_id, conn, session_id)
        conn.execute("UPDATE nodes SET status = 'done', updated_at = ? WHERE id = ? AND status NOT IN ('blocked', 'superseded')", (_now(), state_id))
        conn.execute("UPDATE nodes SET status = 'in_progress', updated_at = ? WHERE id = ? AND status NOT IN ('done', 'blocked', 'superseded')", (_now(), target_id))
        mark_dirty(state_id, conn)
        mark_dirty(target_id, conn)
        _invalidate_graph_cache()
        get_activation(state_id, conn, config)
        get_activation(target_id, conn, config)

        cost_delta = None
        if expected_cost is not None:
            cost_delta = {
                "tokens": cost_actual["tokens"] - expected_cost.get("tokens", 0),
                "risk": cost_actual["risk"] - expected_cost.get("risk", 0.0),
            }

        frontier = _get_frontier_states(src["project"], conn, config)
        _safe_release(conn, "act_tx", owned)
    except OpenPlanError:
        _safe_rollback(conn, "act_tx", owned)
        raise
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
        raise InvalidStateError(state_id)
    if not options:
        raise NoOptionsError()

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

        for s in states_created:
            increment_max_in_degree(s, conn)
            mark_dirty(s, conn)

        _record_event(conn, state_id, project, "branched", {
            "action": "branched", "source": state_id, "branch_id": branch_id,
            "options": len(options), "states_created": states_created,
        }, session_id)
        _safe_release(conn, "branch_tx", owned_branch)
    except OpenPlanError:
        _safe_rollback(conn, "branch_tx", owned_branch)
        raise
    except Exception:
        _safe_rollback(conn, "branch_tx", owned_branch)
        raise

    mark_dirty(state_id, conn)
    _invalidate_graph_cache()

    return {"ok": True, "branch_id": branch_id, "options": len(options), "states_created": states_created}


def abandon(state_id: str, conn: sqlite3.Connection, session_id: str = "") -> dict[str, Any]:
    node = conn.execute("SELECT * FROM nodes WHERE id = ?", (state_id,)).fetchone()
    if not node:
        raise InvalidStateError(state_id)

    all_descendants = set()
    stack = [state_id]
    while stack:
        nid = stack.pop()
        children = conn.execute("SELECT target_id FROM edges WHERE source_id = ?", (nid,)).fetchall()
        for c in children:
            tid = c["target_id"]
            if tid not in all_descendants:
                all_descendants.add(tid)
                stack.append(tid)

    affected = [state_id] + list(all_descendants)
    now = _now()
    for nid in affected:
        conn.execute("UPDATE nodes SET status = 'superseded', updated_at = ? WHERE id = ?", (now, nid))
        mark_dirty(nid, conn)

    project = node["project"]
    _record_event(conn, state_id, project, "abandoned", {
        "action": "abandoned",
        "state_id": state_id,
        "states_affected": len(affected),
    }, session_id)
    _invalidate_graph_cache()

    return {"ok": True, "state_id": state_id, "states_affected": len(affected)}
