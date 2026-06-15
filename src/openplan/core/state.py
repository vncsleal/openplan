from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

_log = logging.getLogger("openplan.state")

from openplan.core.activation import get_activation, mark_dirty
from openplan.core.conditions import _check_postconditions, _check_preconditions
from openplan.core.costs import _ACTION_COST_DEFAULTS, _get_default_cost, _update_cost_baseline, _auto_calibrate_edge, _chain_calibrate
from openplan.core.errors import (
    CycleDetectedError, InvalidActionError, InvalidStateError,
    OpenPlanError, TerminalStateError,
)
from openplan.core.graph import _get_frontier_states, _invalidate_graph_cache
from openplan.core.graph_ops import _detect_cycle, _prune_stale_branches, _nearest_active_ancestor
from openplan.core.ids import _ensure_node, generate_id
from openplan.core.planning import init_project, branch  # noqa: F401
from openplan.core.reasoning import ReasoningPayload
from openplan.core.transaction import _now, _record_event, _safe_release, _safe_rollback, _safe_savepoint


# Re-export planning functions for backward compatibility
from openplan.core.planning import generate_phases, plan_project, _infer_action, _insert_goal_markers  # noqa: F401, E402
from openplan.core.goals import _parse_goal_markers  # noqa: F401, E402
from openplan.core.costs import _get_default_cost  # noqa: F401, E402
from openplan.core.transaction import _now, _record_event, _safe_release, _safe_rollback, _safe_savepoint  # noqa: F401, E402


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
        from openplan.core.state import abandon as _abandon
        return _abandon(state_id, conn, session_id=session_id)
    if kind == "revert":
        from openplan.core.state import revert as _revert
        return _revert(state_id, conn, session_id=session_id)
    if kind == "branch":
        if not options:
            raise __import__("openplan.core.errors", fromlist=["NoOptionsError"]).NoOptionsError()
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
                available = conn.execute(
                    "SELECT e.action, e.target_id, COALESCE(n.label, '') AS label FROM edges e LEFT JOIN nodes n ON n.id = e.target_id WHERE e.source_id = ? ORDER BY e.prob DESC LIMIT 16",
                    (state_id,),
                ).fetchall()
                raise InvalidActionError(state_id, action, [(r["action"], r["target_id"], r["label"]) for r in available])
            if len(matching) > 1:
                matching = sorted(matching, key=lambda e: (-e["prob"], e["cost_tokens"]))
            target_id = dict(matching[0])["target_id"]

        edge = conn.execute(
            "SELECT * FROM edges WHERE source_id = ? AND target_id = ? AND action = ?",
            (state_id, target_id, action),
        ).fetchone()
        if not edge:
            available = conn.execute(
                "SELECT e.action, e.target_id, COALESCE(n.label, '') AS label FROM edges e LEFT JOIN nodes n ON n.id = e.target_id WHERE e.source_id = ? ORDER BY e.prob DESC LIMIT 16",
                (state_id,),
            ).fetchall()
            raise InvalidActionError(state_id, action, [(r["action"], r["target_id"], r["label"]) for r in available])
        edge = dict(edge)

        _check_preconditions(edge, conn)

        if _detect_cycle(conn, state_id, target_id, action):
            available = conn.execute(
                "SELECT e.action, e.target_id, COALESCE(n.label, '') AS label FROM edges e LEFT JOIN nodes n ON n.id = e.target_id WHERE e.source_id = ? AND e.target_id != ? ORDER BY e.prob DESC LIMIT 16",
                (state_id, target_id),
            ).fetchall()
            raise CycleDetectedError(state_id, target_id, [(r["action"], r["target_id"], r["label"]) for r in available])

        cost_value = actual_cost.get("tokens", edge.get("cost_tokens", 10000)) if actual_cost else edge.get("cost_tokens", 10000)
        cost_source = "agent" if actual_cost else "auto"
        payload = {
            "action": action, "source": state_id, "target": target_id,
            "evidence": evidence, "thought": thought, "expected_cost": expected_cost,
            "cost_actual": {"tokens": cost_value, "risk": edge.get("cost_risk", 0.1)},
            "cost_source": cost_source,
        }
        event_id = _record_event(conn, state_id, src["project"], "acted", payload, session_id)
        _increment_visit = __import__("openplan.core.ids", fromlist=["_increment_visit"])._increment_visit
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
        if not project_type:
            p_row = conn.execute(
                "SELECT project_type FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
                (src["project"],),
            ).fetchone()
            if p_row:
                project_type = p_row["project_type"] or ""
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


def abandon(state_id: str, conn: sqlite3.Connection, session_id: str = "") -> dict[str, Any]:
    src = conn.execute("SELECT * FROM nodes WHERE id = ?", (state_id,)).fetchone()
    if not src:
        raise InvalidStateError(state_id)
    project = src["project"]
    conn.execute(
        "UPDATE nodes SET status = 'superseded', updated_at = ? "
        "WHERE id IN (WITH RECURSIVE subtree(id) AS (SELECT ? UNION ALL SELECT e.target_id FROM edges e JOIN subtree s ON e.source_id = s.id) SELECT id FROM subtree)",
        (_now(), state_id),
    )
    _record_event(conn, state_id, project, "abandoned", {"action": "abandon", "state_id": state_id}, session_id)
    ancestor = _nearest_active_ancestor(state_id, conn)
    return {"ok": True, "state_id": state_id, "states_affected": conn.changes, "cursor_moved": {"from": state_id, "to": ancestor}}


def revert(state_id: str, conn: sqlite3.Connection, session_id: str = "") -> dict[str, Any]:
    conn.execute("UPDATE nodes SET status = 'superseded', updated_at = ? WHERE id = ?", (_now(), state_id))
    incoming = conn.execute(
        "SELECT source_id FROM edges WHERE target_id = ? ORDER BY updated_at DESC LIMIT 1",
        (state_id,),
    ).fetchone()
    if not incoming:
        return {"ok": True, "state_id": state_id, "error": "no predecessor"}
    pred_id = incoming["source_id"]
    pred = conn.execute("SELECT status FROM nodes WHERE id = ?", (pred_id,)).fetchone()
    if pred and pred["status"] == "superseded":
        pred_id = _nearest_active_ancestor(pred_id, conn)
    conn.execute("UPDATE nodes SET status = 'in_progress', updated_at = ? WHERE id = ?", (_now(), pred_id))
    return {"ok": True, "next_state": pred_id, "cursor_moved": {"from": state_id, "to": pred_id}}
