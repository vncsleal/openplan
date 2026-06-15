from __future__ import annotations

import json
import os
import re
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

from mcp.types import CallToolResult

from openplan.core.graph import _graph_health
from openplan.core.read import read_state as _read_state
from openplan.core.state import act as _act
from openplan.core.state import abandon as _abandon
from openplan.core.state import branch as _branch
from openplan.core.state import init_project as _init
from openplan.core.state import _insert_goal_markers
from openplan.core.export import prune as _prune
from openplan.core.telemetry import capture as _capture_event
from openplan.handler_utils import (
    _write_lock_acquire, _write_lock_release, _set_cursor, _get_cursor,
    _resolve_target_id, _check_goal_markers, _store_evidence,
    ok, err, get_conn, get_config, get_session_id, _push_resource_notification,
)


async def handle_act(args: dict) -> CallToolResult:
    project = args["project"]
    _write_lock_acquire()
    need_notify = False
    auto_tuned = False
    did_mutate = False
    try:
        conn = get_conn()
        action = args.get("action", "implement")
        dry_run = args.get("dry_run", False)
        status = args.get("status")
        source = _get_cursor(project)
        if not source:
            root = conn.execute("SELECT id FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1", (project,)).fetchone()
            source = root["id"] if root else None
        if not source:
            return err("NO_CURSOR", f"No position in {project} — call init first")

        if action == "abandon":
            target_input = args.get("target") or source
            target_id = _resolve_target_id(project, target_input, conn)
            result = _abandon(target_id, conn, session_id=get_session_id())
            need_notify = True
        elif action == "revert":
            target_input = args.get("target") or source
            target_id = _resolve_target_id(project, target_input, conn)
            result = _act(target_id, action, conn, get_config(), kind="revert", session_id=get_session_id())
            need_notify = True
            did_mutate = True
        elif action == "prune":
            target_id = args.get("target") or source
            result = _prune(target_id, conn, get_config(), summary_label=args.get("summary_label"), keep_events=args.get("keep_events", False), session_id=get_session_id())
            need_notify = bool(result.get("ok"))
            if result.get("ok"):
                root_row = conn.execute("SELECT id FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1", (project,)).fetchone()
                if root_row:
                    result["cursor"] = root_row["id"]
        elif action == "set_goal":
            goal = args.get("target", "")
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                         (f"goal:{project}", json.dumps({"text": goal, "target_state_id": None})))
            conn.execute("DELETE FROM goal_markers WHERE project = ?", (project,))
            _insert_goal_markers(project, goal, conn)
            result = {"ok": True, "goal": goal}
        elif action == "goal_reached":
            target_state = args.get("target")
            if not target_state:
                return err("NO_TARGET", "goal_reached requires a target state ID")
            goal_row = conn.execute("SELECT value FROM meta WHERE key = ?", (f"goal:{project}",)).fetchone()
            if goal_row:
                try:
                    gv = json.loads(goal_row["value"])
                    gv["target_state_id"] = target_state
                    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                                 (f"goal:{project}", json.dumps(gv)))
                except (json.JSONDecodeError, TypeError):
                    pass
            result = {"ok": True, "goal_satisfied": True, "target_state_id": target_state}
        elif args.get("options"):
            result = _branch(source, args["options"], conn, get_config(), session_id=get_session_id(), parallel=args.get("parallel", False))
            created = result.get("states_created", [])
            if created:
                _set_cursor(project, created[0])
                result["next_state"] = created[0]
            need_notify = True
            did_mutate = True
        elif dry_run:
            target_id = args.get("target") or source
            result = _read_state(target_id, conn)
        elif status or args.get("props_patch"):
            target_input = args.get("target")
            if target_input and not re.match(r'^S-\d{6}$', target_input):
                create_result = _act(source, action, conn, get_config(), target=target_input,
                                     session_id=get_session_id())
                new_id = create_result.get("next_state")
                if new_id and status:
                    result = _act(new_id, action, conn, get_config(), kind="status",
                                 status=status, props_patch=args.get("props_patch"),
                                 session_id=get_session_id())
                    result["created_by"] = new_id
                else:
                    result = create_result
            else:
                target_id = target_input or source
                auto_verify = args.get("auto_verify", False)
                if auto_verify and status == "done":
                    evidence_list = args.get("evidence", [])
                    all_verified = True
                    for ev in evidence_list if isinstance(evidence_list, list) else [evidence_list]:
                        if ev.get("type") == "file" and ev.get("uri"):
                            try:
                                os.stat(ev["uri"])
                            except OSError:
                                all_verified = False
                                break
                    if not all_verified:
                        return err("VERIFICATION_FAILED",
                            f"Cannot mark {target_input or source} as done: file evidence failed disk verification. "
                            "Set auto_verify=false to bypass, or provide valid file paths.")

                result = _act(target_id, action, conn, get_config(), kind="status",
                             status=status, props_patch=args.get("props_patch"),
                             session_id=get_session_id())
            need_notify = True
            target_state_id = target_input or source
            if status == "done":
                now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%fZ")
                label_row = conn.execute(
                    "SELECT label FROM nodes WHERE id = ?", (target_state_id,)
                ).fetchone()
                if label_row and label_row["label"]:
                    _check_goal_markers(conn, project, target_state_id, label_row["label"], now_ts)
            evidence_list = args.get("evidence")
            if evidence_list:
                ev_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                _store_evidence(conn, project, target_state_id, evidence_list, ev_ts)
                result["evidence_stored"] = True
        elif action == "verify":
            target_input = args.get("target") or source
            target_id = _resolve_target_id(project, target_input, conn)
            evidence_list = args.get("evidence")
            verif_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            if evidence_list:
                _store_evidence(conn, project, target_id, evidence_list, verif_now)
                result = {"ok": True, "state_id": target_id, "evidence_stored": True}
            else:
                evidence_rows = [dict(r) for r in conn.execute(
                    "SELECT id, evidence_type, uri, description, status, metadata, created_at FROM evidence WHERE state_id = ? ORDER BY created_at",
                    (target_id,),
                ).fetchall()]
                result = {"ok": True, "state_id": target_id, "evidence": evidence_rows}
            need_notify = True
            did_mutate = True

            evidence_rows = conn.execute(
                "SELECT id, description FROM evidence WHERE state_id = ? AND status = 'verified'",
                (target_id,),
            ).fetchall()
            for er in evidence_rows:
                desc = er["description"]
                conn.execute(
                    "UPDATE goal_markers SET achieved = 1, achieved_at = ?, achieved_by = ? "
                    "WHERE project = ? AND ? LIKE '%' || criterion || '%' AND achieved = 0",
                    (verif_now, target_id, project, desc.lower()),
                )

            satisfies = args.get("satisfies_goal")
            if satisfies:
                conn.execute(
                    "UPDATE goal_markers SET achieved = 1, achieved_at = ?, achieved_by = ? "
                    "WHERE project = ? AND LOWER(criterion) = LOWER(?) AND achieved = 0",
                    (verif_now, target_id, project, satisfies),
                )
        else:
            parent = args.get("parent")
            if parent:
                p_row = conn.execute("SELECT project FROM nodes WHERE id = ?", (parent,)).fetchone()
                if not p_row:
                    return err("INVALID_PARENT", f"Parent state {parent} not found")
                if p_row["project"] != project:
                    return err("PARENT_PROJECT_MISMATCH", f"Parent {parent} belongs to project '{p_row['project']}', not '{project}'")
                source = parent
            actual_cost = args.get("actual_cost")
            if actual_cost is None and args.get("expected_cost"):
                ec = args["expected_cost"]
                actual_cost = {"tokens": ec.get("tokens", 1000), "risk": ec.get("risk", 0.1)}
            result = _act(source, action, conn, get_config(), target=args.get("target"),
                         evidence=args.get("evidence"), thought=args.get("thought"),
                         expected_cost=args.get("expected_cost"), actual_cost=actual_cost,
                         session_id=get_session_id(), postconditions=args.get("postconditions"))
            need_notify = bool(result.get("next_state"))
            did_mutate = True
            if result.get("ok"):
                now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%fZ")
                src_row = conn.execute("SELECT label FROM nodes WHERE id = ?", (source,)).fetchone()
                if src_row and src_row["label"]:
                    _check_goal_markers(conn, project, source, src_row["label"], now_ts)

        if did_mutate and result.get("ok"):
            project_type = conn.execute(
                "SELECT project_type FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
                (project,),
            ).fetchone()
            pt = (project_type["project_type"] or "") if project_type else ""
            cost_actual = result.get("cost_actual", {})
            if cost_actual:
                _capture_event(
                    conn,
                    project_type=pt,
                    action=action,
                    actual_cost=cost_actual.get("tokens", 0),
                    expected_cost=args.get("expected_cost", {}).get("tokens") if args.get("expected_cost") else None,
                    outcome="success",
                )

        if need_notify and result.get("cursor_moved"):
            cm = result["cursor_moved"]
            _set_cursor(project, cm["to"])
        elif need_notify and result.get("next_state"):
            _set_cursor(project, result["next_state"])
        elif need_notify and result.get("cursor"):
            _set_cursor(project, result["cursor"])
        elif need_notify and result.get("state_id"):
            _set_cursor(project, result["state_id"])

        if did_mutate:
            from openplan.core.learning import tune as _tune
            tune_interval = get_config().get("tune_interval", 10)
            if tune_interval > 0:
                count_row = get_conn().execute("SELECT value FROM meta WHERE key = 'self_tuning:act_count'").fetchone()
                count = json.loads(count_row["value"]) if count_row else 0
                count += 1
                if count >= tune_interval:
                    _tune(conn, get_config())
                    count = 0
                    auto_tuned = True
                get_conn().execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('self_tuning:act_count', ?)", (json.dumps(count),))
    finally:
        _write_lock_release()
    if need_notify:
        await _push_resource_notification(project)
    if auto_tuned:
        result["auto_tuned"] = True
    return ok(result, project=project)
