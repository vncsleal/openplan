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


def _auto_calibrate_edge(conn: sqlite3.Connection, edge: dict, target_id: str, outcome: str = "success", actual_cost: float | None = None, source: str = "auto") -> None:
    wh_raw = edge.get("weight_history") or "[]"
    try:
        wh = json.loads(wh_raw) if isinstance(wh_raw, str) else (wh_raw or [])
    except (json.JSONDecodeError, TypeError):
        wh = []
    if source != "agent":
        has_real = any(not e.get("auto") for e in wh)
        if has_real:
            return
    cost_value = actual_cost if actual_cost is not None else edge.get("cost_tokens", 10000)
    now = _now()
    wh.append({
        "actual_cost": {"tokens": cost_value},
        "expected_cost": {"tokens": cost_value},
        "outcome": outcome,
        "source": source,
        "learned_at": now,
    })
    conn.execute(
        "UPDATE edges SET weight_history = ?, updated_at = ? WHERE source_id = ? AND target_id = ? AND action = ?",
        (json.dumps(wh), now, edge["source_id"], target_id, edge["action"]),
    )


def _chain_calibrate(conn: sqlite3.Connection, project: str, current_event_id: str) -> None:
    prev = conn.execute(
        "SELECT payload, node_id FROM events WHERE project = ? AND event_type = 'acted' "
        "AND id != ? ORDER BY created_at DESC LIMIT 1",
        (project, current_event_id),
    ).fetchone()
    if not prev:
        return
    try:
        prev_payload = json.loads(prev["payload"]) if isinstance(prev["payload"], str) else prev["payload"]
    except (json.JSONDecodeError, TypeError):
        return
    prev_from = prev_payload.get("source")
    prev_to = prev_payload.get("target")
    prev_action = prev_payload.get("action")
    if not all([prev_from, prev_to, prev_action]):
        return
    prev_edge = conn.execute(
        "SELECT * FROM edges WHERE source_id = ? AND target_id = ? AND action = ?",
        (prev_from, prev_to, prev_action),
    ).fetchone()
    if not prev_edge:
        return
    _auto_calibrate_edge(conn, dict(prev_edge), prev_to, outcome="success")


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


def _parse_goal_markers(goal: str) -> list[str]:
    parts = re.split(r'[,;.]+', goal)
    markers: list[str] = []
    for p in parts:
        p = p.strip().lower()
        if not p:
            continue
        for prefix in ("that ", "which ", "a ", "an ", "the "):
            if p.startswith(prefix):
                p = p[len(prefix):]
                break
        if p and len(p) > 3:
            markers.append(p)
    return markers


def _insert_goal_markers(project: str, goal: str, conn: sqlite3.Connection) -> None:
    markers = _parse_goal_markers(goal)
    for criterion in markers:
        conn.execute(
            "INSERT OR IGNORE INTO goal_markers (project, criterion) VALUES (?, ?)",
            (project, criterion),
        )


def init_project(project: str, label: str | None, conn: sqlite3.Connection, session_id: str = "", project_type: str = "", goal: str = "") -> dict[str, Any]:
    owned_init = _safe_savepoint(conn, "init_tx")
    try:
        existing = conn.execute(
            "SELECT id, label FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
            (project,),
        ).fetchone()
        if existing:
            if goal:
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    (f"goal:{project}", json.dumps({"text": goal, "target_state_id": None})),
                )
                conn.execute("DELETE FROM goal_markers WHERE project = ?", (project,))
                _insert_goal_markers(project, goal, conn)
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
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (f"goal:{project}", json.dumps({"text": goal, "target_state_id": None})),
            )
            _insert_goal_markers(project, goal, conn)
            _record_event(conn, sid, project, "goal_set", {"goal": goal}, session_id)
        conn.execute("UPDATE nodes SET updated_at = ? WHERE id = ?", (now, sid))
        if project_type:
            for bl in conn.execute(
                "SELECT action, cost_tokens, cost_risk, sample_count FROM cost_baselines "
                "WHERE project IS NULL AND project_type = ?",
                (project_type,),
            ).fetchall():
                conn.execute(
                    "INSERT OR IGNORE INTO cost_baselines "
                    "(project, project_type, action, cost_tokens, cost_risk, sample_count, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (project, project_type, bl["action"], bl["cost_tokens"],
                     bl["cost_risk"], bl["sample_count"], now),
                )
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


def _check_postconditions(postconditions: dict, target_id: str, conn: sqlite3.Connection) -> None:
    if not postconditions:
        return
    row = conn.execute("SELECT props FROM nodes WHERE id = ?", (target_id,)).fetchone()
    if not row:
        return
    try:
        current_props = json.loads(row["props"]) if isinstance(row["props"], str) else row["props"]
    except (json.JSONDecodeError, TypeError):
        current_props = {}
    for key, expected_value in postconditions.items():
        actual_value = current_props.get(key)
        if actual_value is not None and actual_value != expected_value:
            raise PreconditionError(target_id, "postcondition", f"{key}={expected_value} (actual={actual_value})")


def _upsert_baseline(conn: sqlite3.Connection, project: str | None, project_type: str, action: str, cost_tokens: float, cost_risk: float) -> None:
    row = conn.execute(
        "SELECT cost_tokens, cost_risk, sample_count FROM cost_baselines "
        "WHERE project IS NOT DISTINCT FROM ? AND project_type = ? AND action = ?",
        (project, project_type, action),
    ).fetchone()
    if row:
        n = row["sample_count"] + 1
        avg_tokens = (row["cost_tokens"] * row["sample_count"] + cost_tokens) / n
        avg_risk = (row["cost_risk"] * row["sample_count"] + cost_risk) / n
        conn.execute(
            "UPDATE cost_baselines SET cost_tokens = ?, cost_risk = ?, sample_count = ?, updated_at = ? "
            "WHERE project IS NOT DISTINCT FROM ? AND project_type = ? AND action = ?",
            (round(avg_tokens, 2), round(avg_risk, 4), n, _now(), project, project_type, action),
        )
    else:
        conn.execute(
            "INSERT OR IGNORE INTO cost_baselines (project, project_type, action, cost_tokens, cost_risk, sample_count, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (project, project_type, action, cost_tokens, cost_risk, _now()),
        )


def _update_cost_baseline(project: str, project_type: str, action: str, cost_tokens: float, cost_risk: float, conn: sqlite3.Connection) -> None:
    if project:
        _upsert_baseline(conn, project, project_type, action, cost_tokens, cost_risk)
    if project_type:
        _upsert_baseline(conn, None, project_type, action, cost_tokens, cost_risk)


def act(
    state_id: str,
    action: str,
    conn: sqlite3.Connection,
    config: dict[str, Any],
    target: str | None = None,
    evidence: str | None = None,
    thought: str | None = None,
    expected_cost: dict | None = None,
    actual_cost: dict | None = None,
    session_id: str = "",
    reasoning: dict | None = None,
    postconditions: dict | None = None,
    kind: str = "transition",
    status: str | None = None,
    props_patch: dict | None = None,
    options: list[dict] | None = None,
) -> dict[str, Any]:
    if kind == "abandon":
        return abandon(state_id, conn, session_id=session_id)
    if kind == "revert":
        return revert(state_id, conn, session_id=session_id)
    if kind == "branch":
        if not options:
            from openplan.core.errors import NoOptionsError
            raise NoOptionsError()
        return branch(state_id, options, conn, config, session_id=session_id)
    if kind == "status":
        from openplan.core.read import update_state as _update_state_lazy
        return _update_state_lazy(state_id, conn, status=status, props_patch=props_patch, session_id=session_id)
    if kind == "read":
        from openplan.core.read import read_state as _read_state
        return _read_state(state_id, conn)

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
            if tgt:
                target_id = tgt["id"]
            else:
                tgt = conn.execute(
                    "SELECT id FROM nodes WHERE project = ? AND label = ?",
                    (src["project"], target),
                ).fetchone()
                if tgt:
                    target_id = tgt["id"]
                else:
                    tgt = conn.execute(
                        "SELECT id FROM nodes WHERE project = ? AND label LIKE ? LIMIT 1",
                        (src["project"], f"%{target}%"),
                    ).fetchone()
                    if tgt:
                        target_id = tgt["id"]
                    else:
                        target_id = _ensure_node(src["project"], target, conn, parent_id=state_id if state_id != src["id"] else None)
            bl_cost = _get_default_cost(action, src.get("project_type", ""), conn)
            if src.get("project_type"):
                bl = conn.execute(
                    "SELECT cost_tokens FROM cost_baselines WHERE project = ? AND action = ?",
                    (src["project"], action),
                ).fetchone()
                if not bl and src.get("project_type"):
                    bl = conn.execute(
                        "SELECT cost_tokens FROM cost_baselines WHERE project IS NULL AND project_type = ? AND action = ?",
                        (src.get("project_type", ""), action),
                    ).fetchone()
                if bl:
                    bl_cost = bl["cost_tokens"]
            conn.execute(
                "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, prob, created_at, updated_at) VALUES (?, ?, ?, ?, 0.8, ?, ?)",
                (state_id, target_id, action, bl_cost, _now(), _now()),
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

        cost_value = actual_cost.get("tokens", edge.get("cost_tokens", 10000)) if actual_cost else edge.get("cost_tokens", 10000)
        cost_source = "agent" if actual_cost else "auto"
        payload = {
            "action": action, "source": state_id, "target": target_id,
            "evidence": evidence, "thought": thought, "expected_cost": expected_cost,
            "cost_actual": {"tokens": cost_value, "risk": edge.get("cost_risk", 0.1)},
            "cost_source": cost_source,
        }
        event_id = _record_event(conn, state_id, src["project"], "acted", payload, session_id)
        _increment_visit(target_id, conn)
        _auto_calibrate_edge(conn, edge, target_id, outcome="success", actual_cost=cost_value, source=cost_source)
        _chain_calibrate(conn, src["project"], event_id)
        if postconditions:
            _check_postconditions(postconditions, target_id, conn)
            current_props = json.loads(
                conn.execute("SELECT props FROM nodes WHERE id = ?", (target_id,)).fetchone()["props"]
            ) if conn.execute("SELECT props FROM nodes WHERE id = ?", (target_id,)).fetchone() else {}
            merged = dict(current_props)
            merged.update(postconditions)
            conn.execute("UPDATE nodes SET props = ?, updated_at = ? WHERE id = ?",
                         (json.dumps(merged), _now(), target_id))
        project_type = src.get("project_type", "") or ""
        _update_cost_baseline(src["project"], project_type, action, cost_value, edge.get("cost_risk", 0.1), conn)
        _prune_stale_branches(state_id, conn, session_id)
        conn.execute("UPDATE nodes SET status = 'done', updated_at = ? WHERE id = ? AND status NOT IN ('blocked', 'superseded')", (_now(), state_id))
        conn.execute("UPDATE nodes SET status = 'in_progress', updated_at = ? WHERE id = ? AND status NOT IN ('done', 'blocked', 'superseded')", (_now(), target_id))
        mark_dirty(state_id, conn)
        mark_dirty(target_id, conn)
        _invalidate_graph_cache()
        get_activation(state_id, conn, config)
        get_activation(target_id, conn, config)

        goal_satisfied = None
        try:
            achieved = conn.execute(
                "SELECT COUNT(*) AS cnt FROM goal_markers WHERE project = ? AND achieved = 1",
                (src["project"],),
            ).fetchone()
            total = conn.execute(
                "SELECT COUNT(*) AS cnt FROM goal_markers WHERE project = ?",
                (src["project"],),
            ).fetchone()
            if achieved and total and total["cnt"] > 0 and achieved["cnt"] == total["cnt"]:
                goal_satisfied = True
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    (f"goal_satisfied:{src['project']}", json.dumps({"satisfied_at": _now()})),
                )
                _record_event(conn, target_id, src["project"], "goal_satisfied", {
                    "project": src["project"],
                }, session_id)
        except Exception:
            pass

        cost_delta = None
        if expected_cost is not None:
            cost_delta = {
                "tokens": cost_value - expected_cost.get("tokens", 0),
                "risk": edge.get("cost_risk", 0.1) - expected_cost.get("risk", 0.0),
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
        "cost_actual": {"tokens": cost_value, "risk": edge.get("cost_risk", 0.1)}, "cost_delta": cost_delta, "cost_source": cost_source,
        "new_frontier": [s["id"] for s in frontier],
        "goal_satisfied": goal_satisfied if goal_satisfied else None,
    }


def branch(
    state_id: str,
    options: list[dict],
    conn: sqlite3.Connection,
    config: dict[str, Any],
    session_id: str = "",
    parallel: bool = False,
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
        states_created: list[str] = []
        opt_by_sid: dict[str, dict] = {}
        for opt in options:
            sid = generate_id(project, conn)
            label = opt.get("label", "")
            conn.execute(
                "INSERT INTO nodes (id, label, project, props, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (sid, label, project, json.dumps({"boost": True, "boosted_at": now}), now, now),
            )
            opt_by_sid[sid] = opt
            action = opt["action"]
            expected = opt.get("expected_cost")
            if expected and "tokens" in expected:
                cost_tokens = expected["tokens"]
            else:
                cost_tokens = _get_default_cost(action, src.get("project_type", ""), conn)
            cost_risk = expected.get("risk", 0.1) if expected else 0.1
            prob = opt.get("prob", 0.8)
            conn.execute(
                "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (state_id, sid, action, cost_tokens, cost_risk, prob, now, now),
            )
            states_created.append(sid)

        has_user_sequence = any(opt.get("sequence") is not None for opt in options)
        if parallel:
            pass
        elif has_user_sequence:
            sequenced = [(opt, sid) for sid, opt in opt_by_sid.items() if opt.get("sequence") is not None]
            sequenced.sort(key=lambda x: x[0]["sequence"])
            for (opt_a, sid_a), (opt_b, sid_b) in zip(sequenced, sequenced[1:]):
                action_next = opt_b.get("action", "implement")
                conn.execute(
                    "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (sid_a, sid_b, action_next, 1000, 0.1, 0.9, now, now),
                )
        else:
            for i in range(len(states_created) - 1):
                opt_b = opt_by_sid[states_created[i + 1]]
                action_next = opt_b.get("action", "implement")
                conn.execute(
                    "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (states_created[i], states_created[i + 1], action_next, 1000, 0.1, 0.9, now, now),
                )

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


def _nearest_active_ancestor(state_id: str, conn: sqlite3.Connection) -> str:
    project: str | None = None
    visited: set[str] = set()
    stack = [state_id]
    while stack:
        nid = stack.pop()
        if nid in visited:
            continue
        visited.add(nid)
        row = conn.execute("SELECT status, project FROM nodes WHERE id = ?", (nid,)).fetchone()
        if row:
            project = row["project"]
            if row["status"] not in ("superseded", "cascade_blocked"):
                return nid
        edges = conn.execute("SELECT source_id FROM edges WHERE target_id = ?", (nid,)).fetchall()
        for e in edges:
            stack.append(e["source_id"])
    if project:
        root = conn.execute(
            "SELECT id FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
            (project,),
        ).fetchone()
        if root:
            return root["id"]
    return state_id


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

    cursor_move = _nearest_active_ancestor(state_id, conn)

    project = node["project"]
    _record_event(conn, state_id, project, "abandoned", {
        "action": "abandoned",
        "state_id": state_id,
        "states_affected": len(affected),
        "cursor_moved_to": cursor_move,
    }, session_id)
    _invalidate_graph_cache()

    result: dict[str, Any] = {"ok": True, "state_id": state_id, "states_affected": len(affected)}
    if cursor_move and cursor_move != state_id:
        result["cursor_moved"] = {"from": state_id, "to": cursor_move}
        result["cursor"] = cursor_move
    return result


def revert(state_id: str, conn: sqlite3.Connection, session_id: str = "") -> dict[str, Any]:
    node = conn.execute("SELECT * FROM nodes WHERE id = ?", (state_id,)).fetchone()
    if not node:
        raise InvalidStateError(state_id)

    edge = conn.execute(
        "SELECT source_id FROM edges WHERE target_id = ? ORDER BY created_at DESC LIMIT 1",
        (state_id,),
    ).fetchone()

    if not edge:
        return {"ok": False, "error": "No previous state to revert to"}

    previous_id = edge["source_id"]
    now = _now()
    conn.execute("UPDATE nodes SET status = 'superseded', updated_at = ? WHERE id = ?", (now, state_id))

    _record_event(conn, previous_id, node["project"], "reverted", {
        "action": "reverted",
        "from": state_id,
        "to": previous_id,
    }, session_id)

    mark_dirty(state_id, conn)
    mark_dirty(previous_id, conn)
    _invalidate_graph_cache()

    return {"ok": True, "cursor": previous_id, "reverted_from": state_id, "reverted_to": previous_id}
