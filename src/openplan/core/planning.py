from __future__ import annotations

import json
import sqlite3
from typing import Any

from openplan.core.activation import get_activation, increment_max_in_degree, mark_dirty
from openplan.core.costs import _get_default_cost
from openplan.core.errors import InvalidStateError, NoOptionsError, OpenPlanError
from openplan.core.goals import _parse_goal_markers, _insert_goal_markers
from openplan.core.graph import _invalidate_graph_cache
from openplan.core.ids import _ensure_node, generate_branch_id, generate_id
from openplan.core.transaction import _now, _record_event, _safe_release, _safe_rollback, _safe_savepoint
from openplan.core.utils import now as _now_util

_ACTION_KEYWORDS: list[tuple[str, str]] = [
    ("research", "research"), ("explore", "explore"), ("analyze", "analyze"),
    ("investigate", "investigate"), ("design", "design"), ("architect", "design"),
    ("plan", "plan"), ("test", "test"), ("verify", "test"),
    ("validate", "test"), ("check", "test"), ("document", "document"),
    ("write_docs", "document"), ("implement", "implement"),
    ("build", "implement"), ("create", "implement"), ("write", "implement"),
    ("add", "implement"), ("deploy", "deploy"), ("release", "deploy"),
    ("ship", "deploy"), ("publish", "deploy"),
]

_POSITION_ACTIONS: list[str] = ["design", "implement", "implement", "test", "deploy"]


def _infer_action(label: str) -> str:
    lowered = label.lower()
    for keyword, action in _ACTION_KEYWORDS:
        if keyword in lowered:
            return action
    return "implement"


def generate_phases(goal: str, project_type: str, conn: sqlite3.Connection) -> list[dict]:
    markers = _parse_goal_markers(goal)
    seen: set[str] = set()
    phases: list[dict] = []
    for marker in markers:
        dedup_key = marker.strip().lower()
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        action = _infer_action(marker)
        cost = _get_default_cost(action, project_type, conn)
        phases.append({
            "label": marker,
            "action": action,
            "expected_cost": {"tokens": cost, "risk": 0.1},
        })
    if not phases:
        default_phases = [
            ("Design architecture", "design"),
            ("Implement core logic", "implement"),
            ("Write test suite", "test"),
            ("Build and publish", "deploy"),
        ]
        for label, action in default_phases:
            cost = _get_default_cost(action, project_type, conn)
            phases.append({
                "label": label,
                "action": action,
                "expected_cost": {"tokens": cost, "risk": 0.1},
            })
    phase_count = len(phases)
    if phase_count > 1:
        for i, ph in enumerate(phases):
            pos_action = _POSITION_ACTIONS[i] if i < len(_POSITION_ACTIONS) else "implement"
            if ph["action"] != pos_action:
                ph["action"] = pos_action
                ph["expected_cost"]["tokens"] = _get_default_cost(pos_action, project_type, conn)
    return phases


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
            src_type = src["project_type"] if src["project_type"] else ""
            if not src_type:
                p_row = conn.execute(
                    "SELECT project_type FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
                    (project,),
                ).fetchone()
                if p_row:
                    src_type = p_row["project_type"] or ""
            expected = opt.get("expected_cost")
            if expected and "tokens" in expected:
                cost_tokens = expected["tokens"]
            else:
                cost_tokens = _get_default_cost(action, src_type, conn)
            cost_risk = expected.get("risk", 0.1) if expected else 0.1
            prob = opt.get("prob", 0.8)
            conn.execute(
                "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (state_id, sid, action, cost_tokens, cost_risk, prob, now, now),
            )
            states_created.append(sid)

        has_depends = any(opt.get("depends_on") for opt in options)
        if has_depends:
            sid_by_label: dict[str, str] = {}
            for sid, opt in opt_by_sid.items():
                label = opt.get("label", "")
                if label:
                    sid_by_label[label.lower().strip()] = sid
            for sid, opt in opt_by_sid.items():
                deps = opt.get("depends_on", [])
                if isinstance(deps, str):
                    deps = [deps]
                for dep_label in deps:
                    dep_sid = sid_by_label.get(dep_label.lower().strip())
                    if dep_sid:
                        conn.execute(
                            "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (dep_sid, sid, opt.get("action", "implement"), 1000, 0.1, 0.9, now, now),
                        )
        else:
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


def plan_project(project: str, goal: str, conn: sqlite3.Connection, session_id: str = "", project_type: str = "", label: str | None = None) -> dict:
    init_result = init_project(project, label, conn, session_id=session_id, project_type=project_type, goal=goal)
    root_id = init_result["state_id"]
    phases = generate_phases(goal, project_type, conn)
    branch_result = None
    first_phase_id = root_id
    if phases:
        branch_result = branch(root_id, phases, conn, {"activation_sensitivity": 0.5, "learning_rate": 0.2, "tune_interval": 10}, session_id=session_id)
        first_phase_id = branch_result["states_created"][0]

    phases_out = []
    for i, ph in enumerate(phases):
        sid = branch_result["states_created"][i] if branch_result else root_id
        phases_out.append({
            "state_id": sid,
            "label": ph["label"],
            "action": ph["action"],
            "estimated_cost": ph["expected_cost"],
        })

    return {
        "ok": True,
        "project": project,
        "state_id": root_id,
        "label": label or project,
        "project_type": project_type,
        "goal": goal,
        "phases": phases_out,
        "total_estimated_cost": sum(p["expected_cost"]["tokens"] for p in phases),
        "cursor": first_phase_id,
    }
