from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from mcp.types import CallToolResult

from openplan.core.graph import _graph_health
from openplan.core.state import act as _act
from openplan.handler_utils import (
    _write_lock_acquire, _write_lock_release, _set_cursor, _get_cursor,
    _resolve_target_id, _check_goal_markers, _store_evidence,
    ok, err, get_conn, get_config, get_session_id, _push_resource_notification,
)


async def handle_complete(args: dict) -> CallToolResult:
    _write_lock_acquire()
    project = args["project"]
    need_notify = False
    try:
        conn = get_conn()
        state_input = args["state"]

        if re.match(r'^S-\d{6}$', state_input):
            state_id = state_input
        else:
            label_match = conn.execute(
                "SELECT id FROM nodes WHERE project = ? AND LOWER(label) = LOWER(?) ORDER BY created_at DESC LIMIT 1",
                (project, state_input),
            ).fetchone()
            if not label_match:
                label_match = conn.execute(
                    "SELECT id FROM nodes WHERE project = ? AND LOWER(label) LIKE LOWER(?) ORDER BY created_at DESC LIMIT 1",
                    (project, f"%{state_input}%"),
                ).fetchone()
            if not label_match:
                return err("STATE_NOT_FOUND", f"No state matching '{state_input}' found in project '{project}'")
            state_id = label_match["id"]

        current_label = conn.execute("SELECT label FROM nodes WHERE id = ?", (state_id,)).fetchone()
        label_text = current_label["label"] if current_label else ""

        incoming = conn.execute(
            "SELECT e.action, e.source_id FROM edges e WHERE e.target_id = ? ORDER BY e.updated_at DESC LIMIT 5",
            (state_id,),
        ).fetchall()
        action = incoming[0]["action"] if incoming else "implement"

        auto_verify = args.get("auto_verify", False)
        if auto_verify:
            evidence_list = args.get("evidence", [])
            for ev in evidence_list if isinstance(evidence_list, list) else [evidence_list]:
                if ev.get("type") == "file" and ev.get("uri"):
                    try:
                        os.stat(ev["uri"])
                    except OSError:
                        return err("VERIFICATION_FAILED",
                            f"Cannot complete {state_id} ('{label_text}'): file evidence '{ev['uri']}' not found on disk. "
                            "Set auto_verify=false to bypass.")

        incoming_edge = conn.execute(
            "SELECT e.cost_tokens, e.action FROM edges e WHERE e.target_id = ? ORDER BY e.updated_at DESC LIMIT 1",
            (state_id,),
        ).fetchone()
        expected = None
        if incoming_edge:
            expected = {"tokens": incoming_edge["cost_tokens"], "risk": 0.1}

        actual_cost = args.get("actual_cost")
        done_result = _act(state_id, action, conn, get_config(), kind="status",
                          status="done", evidence=args.get("evidence"),
                          expected_cost=expected,
                          actual_cost=actual_cost,
                          session_id=get_session_id())

        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%fZ")
        if label_text:
            _check_goal_markers(conn, project, state_id, label_text, now_ts)

        evidence_list = args.get("evidence")
        if evidence_list:
            _store_evidence(conn, project, state_id, evidence_list, now_ts)

        next_edge = conn.execute(
            "SELECT e.target_id, e.action, e.cost_tokens, n.label FROM edges e "
            "JOIN nodes n ON n.id = e.target_id "
            "WHERE e.source_id = ? AND e.action IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM nodes n2 WHERE n2.id = e.target_id AND n2.status = 'done') "
            "ORDER BY e.prob DESC, e.cost_tokens ASC LIMIT 1",
            (state_id,),
        ).fetchone()

        result = {"ok": True, "completed_state": state_id, "completed_label": label_text}

        if next_edge:
            actual_cost = args.get("actual_cost")
            try:
                edge_cost = next_edge["cost_tokens"]
            except (IndexError, KeyError, TypeError):
                edge_cost = None
            act_result = _act(state_id, next_edge["action"], conn, get_config(),
                             target=next_edge["target_id"],
                             expected_cost={"tokens": edge_cost, "risk": 0.1} if edge_cost is not None else None,
                             actual_cost=actual_cost,
                             session_id=get_session_id())
            if act_result.get("next_state"):
                _set_cursor(project, act_result["next_state"])
                result["next_state"] = act_result["next_state"]
                result["next_label"] = next_edge["label"] or ""
                result["next_action"] = next_edge["action"]

            remaining = conn.execute(
                "SELECT COUNT(*) AS cnt FROM nodes WHERE project = ? AND status = 'pending'",
                (project,),
            ).fetchone()
            result["remaining_phases"] = remaining["cnt"] if remaining else 0
            result["completed_plan"] = (remaining["cnt"] if remaining else 0) == 0
        else:
            _set_cursor(project, state_id)
            result["completed_plan"] = True
            result["remaining_phases"] = 0

        health = _graph_health(project, conn, state_id)
        result["project_health"] = health
        need_notify = True
    finally:
        _write_lock_release()
    if need_notify:
        await _push_resource_notification(project)
    return ok(result, project=project)
