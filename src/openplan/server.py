from __future__ import annotations

import atexit
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger("openplan")

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult, GetPromptResult, ListResourcesResult, Prompt,
    PromptMessage, PromptArgument, ReadResourceResult, Resource,
    ResourceUpdatedNotification, ResourceUpdatedNotificationParams,
    ServerCapabilities, ServerNotification, TextContent, TextResourceContents,
    ToolsCapability, ResourcesCapability,
)

from openplan import VERSION
from openplan.config import load_config
from openplan.core.analytics import compute_analytics
from openplan.core.errors import OpenPlanError
from openplan.core.graph import _graph_health
from openplan.core.insight_propagation import propagate as _propagate
from openplan.core.learning import tune as _tune
from openplan.core.maintenance import _run_cycle as _maintenance_cycle
from openplan.core.planner import plan as _plan
from openplan.core.read import reconstruct as _reconstruct
from openplan.core.read import update_state as _update_state
from openplan.core.recommend import recommend as _recommend
from openplan.core.recommend import recommend_all as _recommend_all
from openplan.core.state import act as _act
from openplan.core.state import abandon as _abandon
from openplan.core.state import init_project as _init
from openplan.core.state import branch as _branch
from openplan.core.state import _insert_goal_markers
from openplan.core.export import prune as _prune
from openplan.core.simulate import simulate as _simulate
from openplan.core.tree import build_tree as _tree
from openplan.core.telemetry import get_telemetry
from openplan.core.telemetry_client import get_telemetry_client as _get_telemetry_client
from openplan.db.connection import get_connection
from openplan.db.schema import init_db
from openplan.tools.definitions import get_tools

from pydantic import AnyUrl

_config: dict[str, Any] = {}
_conn: Any = None
_write_lock = threading.Lock()
_read_lock = threading.Lock()
_read_count = 0
_notification_queue: list[dict[str, Any]] = []
_notification_seen: set[str] = set()
_notification_lock = threading.Lock()
_maintenance_stop = threading.Event()
_telemetry = get_telemetry()
_telemetry_client = _get_telemetry_client()
_SESSION_ID: str = os.environ.get("OPENCODE_SESSION_ID", "")
if not _SESSION_ID:
    _log.info("OPENCODE_SESSION_ID not set — will generate persistent session ID from DB")
_RESOURCE_PAGE_SIZE = 20
app = Server("openplan")


def _get_conn() -> Any:
    if _conn is None:
        raise RuntimeError("Database not initialized")
    return _conn


def _read_lock_acquire() -> None:
    global _read_count
    with _read_lock:
        _read_count += 1
        if _read_count == 1:
            _write_lock.acquire()


def _read_lock_release() -> None:
    global _read_count
    with _read_lock:
        _read_count -= 1
        if _read_count == 0:
            _write_lock.release()


def _write_lock_acquire() -> None:
    _write_lock.acquire()


def _write_lock_release() -> None:
    _write_lock.release()


def _resolve_session_id(conn: Any) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key = 'session_id'").fetchone()
    if row:
        return row["value"]
    sid = str(uuid.uuid4())
    conn.execute("INSERT INTO meta (key, value) VALUES ('session_id', ?)", (sid,))
    conn.commit()
    return sid


def _get_cursor(project: str) -> str | None:
    if _conn is None:
        return None
    if _SESSION_ID:
        row = _conn.execute(
            "SELECT cursor_state_id FROM sessions WHERE session_id = ? AND project = ?",
            (_SESSION_ID, project),
        ).fetchone()
        if row:
            return row["cursor_state_id"]
    row = _conn.execute(
        "SELECT cursor_state_id FROM sessions WHERE project = ? ORDER BY updated_at DESC LIMIT 1",
        (project,),
    ).fetchone()
    if row:
        return row["cursor_state_id"]
    row = _conn.execute(
        "SELECT json_extract(payload, '$.target') AS tgt FROM events "
        "WHERE project = ? AND event_type = 'acted' ORDER BY created_at DESC LIMIT 1",
        (project,),
    ).fetchone()
    if row and row["tgt"]:
        return row["tgt"]
    return None


def _set_cursor(project: str, state_id: str) -> None:
    if _conn is None:
        return
    _conn.execute(
        "INSERT OR REPLACE INTO sessions (session_id, project, cursor_state_id, created_at, updated_at) VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
        (_SESSION_ID, project, state_id),
    )


def _resolve_target_id(project: str, target: str, conn: Any) -> str:
    if re.match(r'^S-\d{6}$', target):
        return target
    row = conn.execute(
        "SELECT id FROM nodes WHERE project = ? AND label = ?",
        (project, target),
    ).fetchone()
    if row:
        return row["id"]
    row = conn.execute(
        "SELECT id FROM nodes WHERE project = ? AND label LIKE ? LIMIT 1",
        (project, f"%{target}%"),
    ).fetchone()
    if row:
        return row["id"]
    return target


def _notif_hash(n: dict) -> str:
    return f"{n.get('code', '')}:{n.get('project', '')}"


def _get_fresh_notifications(project: str | None = None) -> list[dict]:
    with _notification_lock:
        fresh = []
        remaining = []
        for n in list(_notification_queue):
            h = _notif_hash(n)
            if h in _notification_seen:
                continue
            if project and n.get("project") and n["project"] != project:
                remaining.append(n)
            else:
                fresh.append(n)
                _notification_seen.add(h)
        _notification_queue.clear()
        _notification_queue.extend(remaining)
    return fresh


def ok(data: dict[str, Any], project: str | None = None) -> CallToolResult:
    enriched = dict(data)
    enriched.setdefault("ok", True)
    if project:
        cursor = _get_cursor(project)
        if cursor:
            enriched["cursor"] = cursor
    notifs = _get_fresh_notifications(project)
    result_str = json.dumps({"ok": True, "data": enriched, **({"_notifications": notifs} if notifs else {})}, default=_j)
    return CallToolResult(
        content=[TextContent(type="text", text=result_str)],
        structuredContent=enriched,
    )


def err(code: str, msg: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"ok": False, "error": {"code": code, "message": msg}}))],
        isError=True,
    )


def _j(o: Any) -> str:
    return o.hex() if isinstance(o, bytes) else str(o)


def _check_goal_markers(conn: Any, project: str, state_id: str, label: str, timestamp: str) -> None:
    label_lower = label.lower()
    for row in conn.execute(
        "SELECT criterion FROM goal_markers WHERE project = ? AND achieved = 0",
        (project,),
    ).fetchall():
        criterion_lower = row["criterion"].lower()
        if criterion_lower in label_lower or label_lower in criterion_lower:
            conn.execute(
                "UPDATE goal_markers SET achieved = 1, achieved_at = ?, achieved_by = ? "
                "WHERE project = ? AND criterion = ?",
                (timestamp, state_id, project, row["criterion"]),
            )


async def _push_resource_notification(project: str) -> None:
    try:
        session = app.request_context.session
        await session.send_notification(
            ServerNotification(
                ResourceUpdatedNotification(
                    params=ResourceUpdatedNotificationParams(
                        uri=AnyUrl(f"openplan://{project}/graph")
                    )
                )
            )
        )
        _log.info("Sent resource update notification for %s", project)
    except Exception:
        _log.warning("Failed to send resource update notification for %s", project)


async def _handle_init(args: dict) -> CallToolResult:
    _write_lock_acquire()
    project = args["project"]
    try:
        result = _init(project, args.get("label"), _get_conn(), session_id=_SESSION_ID, project_type=args.get("project_type", ""), goal=args.get("goal", ""))
        if result.get("state_id"):
            _set_cursor(project, result["state_id"])
    finally:
        _write_lock_release()
    if result.get("state_id"):
        await _push_resource_notification(project)
    return ok(result, project=project)


async def _handle_start(args: dict) -> CallToolResult:
    _write_lock_acquire()
    project = args["project"]
    try:
        from openplan.core.state import plan_project as _plan_project
        result = _plan_project(project, args["goal"], _get_conn(), session_id=_SESSION_ID, project_type=args.get("project_type", ""), label=args.get("label"))
        if result.get("cursor"):
            _set_cursor(project, result["cursor"])
    finally:
        _write_lock_release()
    if result.get("state_id"):
        await _push_resource_notification(project)
    return ok(result, project=project)


async def _handle_complete(args: dict) -> CallToolResult:
    _write_lock_acquire()
    project = args["project"]
    try:
        conn = _get_conn()
        state_input = args["state"]

        # Resolve state: S-XXXXXX ID or label match
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

        # Get state info to infer action from incoming edges
        current_label = conn.execute("SELECT label FROM nodes WHERE id = ?", (state_id,)).fetchone()
        label_text = current_label["label"] if current_label else ""

        # Find the action used to reach this state (prefer incoming edge from a non-root state)
        incoming = conn.execute(
            "SELECT e.action, e.source_id FROM edges e WHERE e.target_id = ? ORDER BY e.updated_at DESC LIMIT 5",
            (state_id,),
        ).fetchall()
        action = incoming[0]["action"] if incoming else "implement"

        # auto_verify check before marking done
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

        # Look up expected_cost from the edge that arrived at this state (for retro calibration)
        incoming_edge = conn.execute(
            "SELECT e.cost_tokens, e.action FROM edges e WHERE e.target_id = ? ORDER BY e.updated_at DESC LIMIT 1",
            (state_id,),
        ).fetchone()
        expected = None
        if incoming_edge:
            expected = {"tokens": incoming_edge["cost_tokens"], "risk": 0.1}

        # Mark state as done with evidence and actual cost
        actual_cost = args.get("actual_cost")
        done_result = _act(state_id, action, conn, _config, kind="status",
                          status="done", evidence=args.get("evidence"),
                          expected_cost=expected,
                          actual_cost=actual_cost,
                          postconditions=args.get("postconditions"),
                          session_id=_SESSION_ID)

        # Check goal markers on completion
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%fZ")
        if label_text:
            _check_goal_markers(conn, project, state_id, label_text, now_ts)

        # Find next phase: sequential edge from this state
        next_edge = conn.execute(
            "SELECT e.target_id, e.action, e.cost_tokens, n.label FROM edges e "
            "JOIN nodes n ON n.id = e.target_id "
            "WHERE e.source_id = ? AND e.action IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM nodes n2 WHERE n2.id = e.target_id AND n2.status = 'done') "
            "ORDER BY e.prob DESC, e.cost_tokens ASC LIMIT 1",
            (state_id,),
        ).fetchone()

        result = {
            "ok": True,
            "completed_state": state_id,
            "completed_label": label_text,
        }

        if next_edge:
            # Traverse to next phase — pass expected_cost from edge for retro calibration
            actual_cost = args.get("actual_cost")
            edge_cost = next_edge["cost_tokens"] if next_edge.get("cost_tokens") else None
            act_result = _act(state_id, next_edge["action"], conn, _config,
                             target=next_edge["target_id"],
                             expected_cost={"tokens": edge_cost, "risk": 0.1} if edge_cost else None,
                             actual_cost=actual_cost,
                             session_id=_SESSION_ID)
            if act_result.get("next_state"):
                _set_cursor(project, act_result["next_state"])
                result["next_state"] = act_result["next_state"]
                result["next_label"] = next_edge["label"] or ""
                result["next_action"] = next_edge["action"]

            # Count remaining phases
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

        # Include project health snapshot
        from openplan.core.graph import _graph_health as _gh
        health = _gh(project, conn, state_id)
        result["project_health"] = health

        need_notify = True
    finally:
        _write_lock_release()
    if need_notify:
        await _push_resource_notification(project)
    return ok(result, project=project)


async def _handle_act(args: dict) -> CallToolResult:
    project = args["project"]
    _write_lock_acquire()
    need_notify = False
    auto_tuned = False
    did_mutate = False
    try:
        conn = _get_conn()
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
            result = _abandon(target_id, conn, session_id=_SESSION_ID)
            need_notify = True
        elif action == "revert":
            target_input = args.get("target") or source
            target_id = _resolve_target_id(project, target_input, conn)
            result = _act(target_id, action, conn, _config, kind="revert", session_id=_SESSION_ID)
            need_notify = True
            did_mutate = True
        elif action == "prune":
            target_id = args.get("target") or source
            result = _prune(target_id, conn, _config, summary_label=args.get("summary_label"), keep_events=args.get("keep_events", False), session_id=_SESSION_ID)
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
            result = _branch(source, args["options"], conn, _config, session_id=_SESSION_ID, parallel=args.get("parallel", False))
            created = result.get("states_created", [])
            if created:
                _set_cursor(project, created[0])
                result["next_state"] = created[0]
            need_notify = True
            did_mutate = True
        elif dry_run:
            from openplan.core.read import read_state as _read_state
            target_id = args.get("target") or source
            result = _read_state(target_id, conn)
        elif status or args.get("props_patch"):
            target_input = args.get("target")
            if target_input and not re.match(r'^S-\d{6}$', target_input):
                create_result = _act(source, action, conn, _config, target=target_input,
                                     session_id=_SESSION_ID)
                new_id = create_result.get("next_state")
                if new_id and status:
                    result = _act(new_id, action, conn, _config, kind="status",
                                 status=status, props_patch=args.get("props_patch"),
                                 session_id=_SESSION_ID)
                    result["created_by"] = new_id
                else:
                    result = create_result
            else:
                target_id = target_input or source
                # auto_verify: refuse to mark done if file evidence doesn't exist on disk
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

                result = _act(target_id, action, conn, _config, kind="status",
                             status=status, props_patch=args.get("props_patch"),
                             session_id=_SESSION_ID)
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
                import uuid as _uuid
                for ev in evidence_list if isinstance(evidence_list, list) else [evidence_list]:
                    eid = str(_uuid.uuid4())[:8]
                    ev_type = ev.get("type", "checkpoint")
                    ev_uri = ev.get("uri", "")
                    ev_desc = ev.get("description", "")
                    ev_status = "verified"
                    metadata_ev = "{}"
                    if ev_type == "file" and ev_uri:
                        try:
                            st = os.stat(ev_uri)
                            metadata_ev = json.dumps({"size": st.st_size, "mtime": st.st_mtime})
                        except OSError:
                            ev_status = "unverified"
                            metadata_ev = json.dumps({"error": "file not found or inaccessible", "uri": ev_uri})
                    conn.execute(
                        "INSERT INTO evidence (id, project, state_id, evidence_type, uri, description, status, metadata, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (eid, project, target_state_id, ev_type, ev_uri, ev_desc, ev_status, metadata_ev, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")),
                    )
                result["evidence_stored"] = True
        elif action == "verify":
            target_input = args.get("target") or source
            target_id = _resolve_target_id(project, target_input, conn)
            evidence_list = args.get("evidence")
            verif_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            if evidence_list:
                import uuid as _uuid
                for ev in evidence_list if isinstance(evidence_list, list) else [evidence_list]:
                    eid = str(_uuid.uuid4())[:8]
                    ev_type = ev.get("type", "checkpoint")
                    ev_uri = ev.get("uri", "")
                    ev_desc = ev.get("description", "")
                    status = "verified"
                    if ev_type == "file" and ev_uri:
                        try:
                            st = os.stat(ev_uri)
                            metadata = json.dumps({"size": st.st_size, "mtime": st.st_mtime})
                        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
                            status = "unverified"
                            metadata = json.dumps({"error": "file not found or inaccessible", "uri": ev_uri})
                    else:
                        metadata = "{}"
                    conn.execute(
                        "INSERT INTO evidence (id, project, state_id, evidence_type, uri, description, status, metadata, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (eid, project, target_id, ev_type, ev_uri, ev_desc, status, metadata, verif_now),
                    )
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
        elif dry_run:
            from openplan.core.read import read_state as _read_state
            target_id = args.get("target") or source
            result = _read_state(target_id, conn)
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
            result = _act(source, action, conn, _config, target=args.get("target"),
                         evidence=args.get("evidence"), thought=args.get("thought"),
                         expected_cost=args.get("expected_cost"), actual_cost=actual_cost,
                         session_id=_SESSION_ID, postconditions=args.get("postconditions"))
            need_notify = bool(result.get("next_state"))
            did_mutate = True
            if result.get("ok"):
                now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%fZ")
                src_row = conn.execute("SELECT label FROM nodes WHERE id = ?", (source,)).fetchone()
                if src_row and src_row["label"]:
                    _check_goal_markers(conn, project, source, src_row["label"], now_ts)
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
            tune_interval = _config.get("tune_interval", 10)
            if tune_interval > 0:
                count_row = _conn.execute("SELECT value FROM meta WHERE key = 'self_tuning:act_count'").fetchone()
                count = json.loads(count_row["value"]) if count_row else 0
                count += 1
                if count >= tune_interval:
                    _tune(conn, _config)
                    count = 0
                    auto_tuned = True
                _conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('self_tuning:act_count', ?)", (json.dumps(count),))
    finally:
        _write_lock_release()
    if did_mutate and _telemetry_client.enabled and result.get("ok"):
        project_type = conn.execute(
            "SELECT project_type FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
            (project,),
        ).fetchone()
        pt = (project_type["project_type"] or "") if project_type else ""
        cost_actual = result.get("cost_actual", {})
        if cost_actual:
            _telemetry_client.record(
                project_type=pt,
                action=action,
                expected_cost=args.get("expected_cost", {}).get("tokens") if args.get("expected_cost") else None,
                actual_cost=cost_actual.get("tokens", 0),
                outcome="success",
            )
    if need_notify:
        await _push_resource_notification(project)
    if auto_tuned:
        result["auto_tuned"] = True
    return ok(result, project=project)


async def _handle_recommend(args: dict) -> CallToolResult:
    _read_lock_acquire()
    try:
        conn = _get_conn()
        project = args.get("project")
        if not project or project == "*":
            results = _recommend_all(conn, _config, goal=args.get("goal"), max_cost=args.get("max_cost"))
            projects_data = [dict(r) for r in conn.execute(
                "SELECT n.project, MIN(n.id) AS root_id, COUNT(DISTINCT n2.id) AS state_count "
                "FROM nodes n LEFT JOIN nodes n2 ON n2.project = n.project "
                "GROUP BY n.project ORDER BY state_count DESC"
            ).fetchall()]
            return ok({"results": results, "count": len(results), "projects": projects_data})

        cursor = args.get("cursor") or _get_cursor(project)
        sequence = args.get("sequence")
        target = args.get("target")
        query = args.get("query")
        top_k = args.get("top_k")
        up_depth = args.get("up_depth")
        risk_adjustment = args.get("risk_adjustment")
        max_cost = args.get("max_cost")
        tree_format = args.get("format", "json")

        result: dict[str, Any] = {"ok": True}
        mode = args.get("mode")

        if mode == "plan":
            from openplan.core.estimator import estimate as _estimate
            project_type = args.get("project_type") or ""
            goal_text = args.get("goal") or ""
            if not project_type:
                row = conn.execute("SELECT project_type FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1", (project,)).fetchone()
                project_type = row["project_type"] or "" if row else ""
            if not goal_text:
                gr = conn.execute("SELECT value FROM meta WHERE key = ?", (f"goal:{project}",)).fetchone()
                if gr:
                    try:
                        gv = json.loads(gr["value"])
                        if isinstance(gv, dict):
                            goal_text = gv.get("text", "")
                    except (json.JSONDecodeError, TypeError):
                        pass
            result.update(_estimate(project_type, goal_text, conn))
            result["mode"] = "plan"
        elif mode == "retro":
            from openplan.core.retro import retro as _retro
            result.update(_retro(project, conn))
            result["mode"] = "retro"
        elif mode == "learnings":
            from openplan.core.learnings import learnings as _learnings
            result.update(_learnings(conn))
            result["mode"] = "learnings"
        elif query:
            from openplan.core.graph import search as _search
            sr = _search(query, conn, project=project, limit=top_k or 20)
            result.update(sr)
        elif sequence:
            sim_result = _simulate(project, sequence, conn, _config, cursor=cursor)
            result.update(sim_result)
            if sim_result.get("total_cost") is not None:
                result["expected_cost"] = {
                    "tokens": sim_result["total_cost"],
                    "prob": sim_result.get("cumulative_prob", 1.0),
                    "steps": sim_result.get("steps", 0),
                }
        elif target:
            constraints: dict[str, Any] = {}
            if top_k:
                constraints["top_k"] = top_k
            if risk_adjustment:
                constraints["risk_adjustment"] = risk_adjustment
            if max_cost:
                constraints["max_cost"] = max_cost
            plan_result = _plan(cursor, target, conn, _config, constraints=constraints or None, session_id=_SESSION_ID)
            result.update(plan_result)
        else:
            rec_result = _recommend(project, conn, _config, goal=args.get("goal"), max_cost=max_cost, cursor=cursor)
            result.update(rec_result)
            if top_k and rec_result.get("target"):
                try:
                    from openplan.core.read import compare_paths as _compare_paths
                    cp = _compare_paths(project, conn, [rec_result["target"]], config=_config, cursor=cursor)
                    if cp.get("results"):
                        result["alternatives"] = cp["results"]
                except Exception:
                    pass

        health = _graph_health(project, conn)
        ev_total = conn.execute("SELECT COUNT(*) AS cnt FROM evidence WHERE project = ?", (project,)).fetchone()["cnt"]
        ev_verified = conn.execute("SELECT COUNT(*) AS cnt FROM evidence WHERE project = ? AND status = 'verified'", (project,)).fetchone()["cnt"]
        result["project_health"] = {
            "total_states": health["state_count"],
            "edge_count": health["edge_count"],
            "max_depth": health["max_depth"],
            "orphan_count": health["orphan_count"],
            "calibration_count": health["calibration_count"],
            "completed": conn.execute("SELECT COUNT(*) AS cnt FROM nodes WHERE project = ? AND status = 'done'", (project,)).fetchone()["cnt"],
            "calibration_rate": round(health["calibration_count"] / health["edge_count"], 4) if health["edge_count"] > 0 else 0.0,
            "evidence_total": ev_total,
            "evidence_verified": ev_verified,
        }
        result["blockers"] = [dict(r) for r in conn.execute(
            "SELECT id, label, status FROM nodes WHERE project = ? AND status IN ('blocked', 'cascade_blocked')",
            (project,),
        ).fetchall()]

        goal_row = conn.execute("SELECT value FROM meta WHERE key = ?", (f"goal:{project}",)).fetchone()
        if goal_row:
            try:
                goal_data = json.loads(goal_row["value"])
                markers = [dict(r) for r in conn.execute(
                    "SELECT criterion, achieved, achieved_at, achieved_by FROM goal_markers WHERE project = ? ORDER BY created_at",
                    (project,),
                ).fetchall()]
                if markers:
                    goal_data["markers"] = {
                        "total": len(markers),
                        "achieved": sum(1 for m in markers if m["achieved"]),
                        "items": markers,
                    }
                result["goal"] = goal_data
                if goal_data.get("markers"):
                    gs = goal_data["markers"].get("achieved", 0) == goal_data["markers"].get("total", 0)
                    if gs:
                        result["goal_satisfied"] = True
                        result["project_complete"] = True
                if goal_data.get("target_state_id"):
                    result["goal_satisfied"] = True
                    result["project_complete"] = True
            except (json.JSONDecodeError, TypeError):
                pass

        bandit_row = conn.execute("SELECT value FROM meta WHERE key = 'self_tuning:bandit'").fetchone()
        if bandit_row:
            try:
                bd = json.loads(bandit_row["value"])
                chosen = bd.get("chosen_arm")
                arms = bd.get("arms", {})
                if chosen and chosen in arms:
                    ab = arms[chosen]
                    alpha = ab.get("alpha", 1)
                    beta = ab.get("beta", 1)
                    tri = alpha + beta - 2
                    ar = alpha / (alpha + beta)
                    cv = "low_data" if tri < 5 else ("converging" if ar > 0.65 else "exploring")
                    cr = conn.execute("SELECT value FROM meta WHERE key='self_tuning:act_count'").fetchone()
                    ac = json.loads(cr["value"]) if cr else 0
                    ti = _config.get("tune_interval", 10)
                    result["self_tuning"] = {
                        "bandit_arm": chosen, "acceptance_rate": round(ar, 3),
                        "convergence": cv, "total_trials": tri,
                        "acts_since_tune": ac, "tune_due": ac >= ti,
                    }
            except (json.JSONDecodeError, TypeError, ZeroDivisionError):
                pass

        est_acc: dict[str, dict[str, float]] = {}
        for r in conn.execute("SELECT key, value FROM meta WHERE key LIKE 'tuning:%'"):
            action = r["key"][7:]
            try:
                td = json.loads(r["value"])
                if td.get("count", 0) > 1 and td.get("avg_cost", 0) > 0:
                    cv_raw = td.get("cost_stddev", 0)
                    est_acc[action] = {
                        "avg_cost": td["avg_cost"], "stddev": cv_raw,
                        "samples": td["count"], "ci": td.get("cost_ci_95"),
                    }
            except (json.JSONDecodeError, TypeError):
                pass
        result["estimation_accuracy"] = est_acc

        by_type: dict[str, dict[str, dict[str, Any]]] = {}
        for r in conn.execute(
            "SELECT project_type, action, cost_tokens, sample_count FROM cost_baselines "
            "WHERE project IS NULL AND project_type != '' AND sample_count > 1"
        ).fetchall():
            pt = r["project_type"]
            action = r["action"]
            by_type.setdefault(pt, {})[action] = {
                "avg_cost": r["cost_tokens"],
                "samples": r["sample_count"],
            }
        if by_type:
            result["estimation_by_type"] = by_type

        if query:
            pass
        elif target or (up_depth is not None and up_depth >= 0):
            tree_target = cursor if not target else target
            try:
                tr = _tree(state_id=tree_target, project=project if not tree_target else None,
                          conn=conn, depth=3, up_depth=up_depth or 0, fmt=tree_format)
                result["tree"] = tr.get("tree")
                result["tree_format"] = tree_format
            except Exception:
                pass

        if not sequence and not target and not query and not cursor:
            result["cursor"] = cursor

        detail = args.get("detail", False)
        if not detail:
            for key in ("bandit_arms", "self_tuning", "estimation_accuracy"):
                result.pop(key, None)

        return ok(result, project=project)
    finally:
        _read_lock_release()


async def _handle_export(args: dict) -> CallToolResult:
    _read_lock_acquire()
    try:
        conn = _get_conn()
        project = args["project"]
        fmt = args.get("format", "json")
        from openplan.core.export import export as _export
        result = _export(project, conn, fmt=fmt)
        result["ok"] = True
        return ok(result, project=project)
    finally:
        _read_lock_release()


HANDLERS = {
    "init": _handle_init,
    "start": _handle_start,
    "complete": _handle_complete,
    "act": _handle_act,
    "recommend": _handle_recommend,
    "export": _handle_export,
}


@app.list_tools()
async def list_tools() -> list:
    return get_tools()


@app.list_resources()
async def list_resources(req: Any = None) -> ListResourcesResult:
    conn = _get_conn()
    cursor = req.params.cursor if req and hasattr(req, "params") and req.params else None
    resources: list[Resource] = [
        Resource(uri="openplan://projects", name="All Projects", description="All projects with state counts", mimeType="application/json"),
        Resource(uri="openplan://analytics", name="Analytics", description="Cross-project analytics and anomaly detection", mimeType="application/json"),
        Resource(uri="openplan://tuning", name="Global Tuning", description="Per-action tuning statistics across all projects", mimeType="application/json"),
    ]
    if cursor:
        project_rows = conn.execute(
            "SELECT DISTINCT project FROM nodes WHERE project > ? ORDER BY project LIMIT ?",
            (cursor, _RESOURCE_PAGE_SIZE),
        ).fetchall()
    else:
        project_rows = conn.execute(
            "SELECT DISTINCT project FROM nodes ORDER BY project LIMIT ?",
            (_RESOURCE_PAGE_SIZE,),
        ).fetchall()
    for row in project_rows:
        p = row["project"]
        resources.append(Resource(uri=f"openplan://{p}/graph", name=f"{p} Graph", description=f"Full graph for {p}", mimeType="application/json"))
        resources.append(Resource(uri=f"openplan://{p}/edges", name=f"{p} Edges", description=f"All edges for {p}", mimeType="application/json"))
        resources.append(Resource(uri=f"openplan://{p}/health", name=f"{p} Health", description=f"Health snapshot for {p}", mimeType="application/json"))
    next_cursor = project_rows[-1]["project"] if len(project_rows) == _RESOURCE_PAGE_SIZE else None
    return ListResourcesResult(resources=resources, nextCursor=next_cursor)


@app.read_resource()
async def read_resource(uri: str) -> ReadResourceResult:
    conn = _get_conn()
    if uri == "openplan://projects":
        projects = [dict(r) for r in conn.execute("SELECT DISTINCT project, COUNT(*) AS state_count FROM nodes GROUP BY project").fetchall()]
        return ReadResourceResult(contents=[TextResourceContents(uri=uri, text=json.dumps(projects), mimeType="application/json")])
    if uri == "openplan://analytics":
        return ReadResourceResult(contents=[TextResourceContents(uri=uri, text=json.dumps(compute_analytics(conn)), mimeType="application/json")])
    if uri == "openplan://tuning":
        tuning_data = {}
        for r in conn.execute("SELECT key, value FROM meta WHERE key LIKE 'tuning:%'"):
            try:
                tuning_data[r["key"][7:]] = json.loads(r["value"])
            except (json.JSONDecodeError, TypeError):
                pass
        return ReadResourceResult(contents=[TextResourceContents(uri=uri, text=json.dumps(tuning_data), mimeType="application/json")])
    m = re.match(r"openplan://(.+?)/graph", uri)
    if m:
        project = m.group(1)
        health = _graph_health(project, conn)
        return ReadResourceResult(contents=[TextResourceContents(uri=uri, text=json.dumps(health), mimeType="application/json")])
    m = re.match(r"openplan://(.+?)/edges", uri)
    if m:
        project = m.group(1)
        edges = [dict(r) for r in conn.execute(
            "SELECT e.*, src.label AS source_label, tgt.label AS target_label "
            "FROM edges e JOIN nodes src ON src.id = e.source_id JOIN nodes tgt ON tgt.id = e.target_id "
            "WHERE src.project = ?", (project,)
        ).fetchall()]
        return ReadResourceResult(contents=[TextResourceContents(uri=uri, text=json.dumps(edges), mimeType="application/json")])
    m = re.match(r"openplan://(.+?)/health", uri)
    if m:
        project = m.group(1)
        health = _graph_health(project, conn)
        return ReadResourceResult(contents=[TextResourceContents(uri=uri, text=json.dumps(health), mimeType="application/json")])
    m = re.match(r"openplan://(.+?)/states/(S-\d{6})$", uri)
    if m:
        project = m.group(1)
        state_id = m.group(2)
        node = conn.execute("SELECT * FROM nodes WHERE id = ? AND project = ?", (state_id, project)).fetchone()
        if node:
            return ReadResourceResult(contents=[TextResourceContents(uri=uri, text=json.dumps(dict(node)), mimeType="application/json")])
    return ReadResourceResult(contents=[TextResourceContents(uri=uri, text=json.dumps({"error": "not found"}), mimeType="application/json")])


@app.list_prompts()
async def list_prompts() -> list[Prompt]:
    return [
        Prompt(
            name="agent_loop",
            title="Agent Loop",
            description="The recommended workflow for using OpenPlan: init → act → recommend → export",
            arguments=[
                PromptArgument(name="project", description="Project slug", required=False),
            ],
        ),
        Prompt(
            name="feature-plan",
            title="Feature Planning",
            description="Plan a new feature: recommend next target, simulate path, then act. Use when starting a new feature or work item.",
            arguments=[
                PromptArgument(name="project", description="Project slug", required=True),
                PromptArgument(name="feature", description="Description of the feature to plan", required=True),
            ],
        ),
        Prompt(
            name="debug-blocked",
            title="Debug Blocked State",
            description="Diagnose a blocked state: read its context, inspect the tree, find alternative paths. Use when a state is blocked or cascade_blocked.",
            arguments=[
                PromptArgument(name="project", description="Project slug", required=True),
                PromptArgument(name="state_id", description="ID of the blocked state", required=True),
            ],
        ),
        Prompt(
            name="review-progress",
            title="Review Project Progress",
            description="Audit project health: reconstruct the full picture, run tuning, check diagnostics. Use periodically or after completing a milestone.",
            arguments=[
                PromptArgument(name="project", description="Project slug", required=True),
            ],
        ),
    ]


@app.get_prompt()
async def get_prompt(name: str, arguments: dict[str, str] | None = None) -> GetPromptResult:
    project = arguments.get("project", "<project>") if arguments else "<project>"

    if name == "feature-plan":
        feature = arguments.get("feature", "the feature") if arguments else "the feature"
        return GetPromptResult(
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=f"""Plan the feature: {feature}

Project: {project}

1. Call recommend(project="{project}") to find the best next target
2. Call act(project="{project}", action="design", target="<describe work>") to branch
3. Call recommend(project="{project}") to update what's next
4. Repeat: act → recommend → act until done

Use the goal parameter via init() if the project has a defined end state.""",
                    ),
                ),
            ],
        )

    if name == "debug-blocked":
        state_id = arguments.get("state_id", "<state_id>") if arguments else "<state_id>"
        return GetPromptResult(
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=f"""Debug blocked state {state_id} in project {project}

1. Call recommend(project="{project}") — blockers are shown in output
2. Call act(project="{project}", target="{state_id}", action="review",
   dry_run=true) to inspect the blocked state
3. Either:
   - act(project="{project}", target="{state_id}", status="pending") to unblock
   - act(project="{project}", action="abandon", target="{state_id}") to drop it
   - act(project="{project}", action="implement", target="<alternative>",
     parent="<parent-id>") to create an alternative path

Check in recommend output for cascade_blocked descendants.""",
                    ),
                ),
            ],
        )

    if name == "review-progress":
        return GetPromptResult(
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=f"""Review progress for project {project}

1. Call recommend(project="{project}", detail=true) for full health + costs + tuning
2. Call export(project="{project}", format="json") for the full graph

Check:
- Calibration rate (should be > 0.5, shown in project_health)
- Blocked states and cascade_blocked propagation
- Self-tuning bandit acceptance rate
- Frontier states (what's actionable)""",
                    ),
                ),
            ],
        )

    return GetPromptResult(
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=f"""You have access to OpenPlan — an MCP server for AI-native project planning.

4 tools:

init(project, label?, project_type?, goal?) — Create a new project (idempotent).
  ALWAYS set project_type — without it, cross-project learning (estimation_by_type,
  learnings, plan mode) cannot accumulate data for this project type.
  Set goal for the desired end state (comma-separated for multiple markers).

act(project, action, target?, parent?, status?, options?, parallel?,
    postconditions?, thought?, evidence?, expected_cost?, actual_cost?,
    satisfies_goal?, dry_run?) — The only mutation tool.
  Sub-operations: traverse, branch (via options with auto-sequence),
  status update, abandon, prune, revert, verify (with satisfies_goal),
  set_goal, dry_run (read without write).

recommend(project?, query?, target?, sequence?, cursor?, detail?) — Read tool.
  Default (no params): best-next-target with A* path + project health.
  query=: full-text search across states.
  target=: A* plan to a specific state.
  sequence=: simulate a chain of actions.
  detail=true: include cost baselines, self-tuning, estimation accuracy.

export(project, format?) — JSON / GraphML / adjacency matrix of the full graph.

Workflow:
  init(project_type=...) → act(options=[...]) → recommend → act(target=..., expected_cost=...) → repeat
  When done: act(action="verify", satisfies_goal="criterion") to tick goal markers.
  Use recommend(query=...) to search past decisions.
  Use export(format="graphml") to visualize the graph externally.

Best practices (learned from self-hosting):
  1. ALWAYS set project_type on init — otherwise your work is invisible to cross-project estimation.
  2. Use satisfies_goal on verify to explicitly tick markers — subtitle matching is fragile.
  3. Pass expected_cost on traverse to get meaningful cost_delta and calibration.

Your current project is `{project}`.""",
                ),
            ),
        ],
    )


def _shutdown() -> None:
    global _conn, _maintenance_stop
    _maintenance_stop.set()
    _telemetry.flush_to_events()
    _telemetry_client.flush()
    if _conn is not None:
        _conn.close()


async def main() -> None:
    global _config, _conn, _SESSION_ID
    _config = load_config()
    _conn = get_connection(_config.get("db_path", "openplan.db"))
    init_db(_conn)
    if not _SESSION_ID:
        _SESSION_ID = _resolve_session_id(_conn)
    _telemetry.set_conn(_conn)
    _telemetry.reload_from_events()

    telem_enabled = _config.get("telemetry_enabled", False)
    telem_endpoint = _config.get("telemetry_endpoint", "")
    if telem_endpoint and not telem_enabled:
        telem_enabled = os.environ.get("OPENPLAN_TELEMETRY_ENABLED", "").lower() in ("1", "true", "yes")
    if not telem_endpoint:
        telem_endpoint = os.environ.get("OPENPLAN_TELEMETRY_ENDPOINT", "")
    if telem_enabled and telem_endpoint:
        _telemetry_client.configure(telem_endpoint, True)
        imported = _telemetry_client.fetch_calibration(_conn)
        if imported:
            _log.info("Telemetry: imported %d global calibration baselines from %s", imported, telem_endpoint)

    atexit.register(_shutdown)

    notifs = _maintenance_cycle(_conn, _config, _write_lock)
    with _notification_lock:
        _notification_queue.extend(notifs)
    _write_lock_acquire()
    try:
        _propagate(_conn, _config)
        _tune(_conn, _config)
    finally:
        _write_lock_release()

    maintenance_thread = threading.Thread(
        target=_maintenance_loop,
        args=(_conn, _config, _write_lock, _notification_queue, _notification_lock, _maintenance_stop),
        daemon=True,
    )
    maintenance_thread.start()

    from openplan.core.embedding import warmup_embeddings
    warmup_embeddings()

    @app.call_tool()
    async def call_tool(name: str, arguments: dict) -> CallToolResult:
        handler = HANDLERS.get(name)
        if not handler:
            return err("UNKNOWN", f"Unknown tool: {name}")
        try:
            return await handler(arguments)
        except OpenPlanError as e:
            return err(e.code, e.message)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            return err("INTERNAL_ERROR", str(e))

    async with stdio_server() as (read, write):
        await app.run(
            read,
            write,
            InitializationOptions(
                server_name="openplan",
                server_version=VERSION,
                capabilities=ServerCapabilities(tools=ToolsCapability(listChanged=True), resources=ResourcesCapability(listChanged=True, subscribe=True)),
            ),
        )


def _maintenance_loop(conn: Any, config: dict, write_lock: threading.Lock, queue: list, queue_lock: threading.Lock, stop_event: threading.Event) -> None:
    interval = config.get("maintenance_interval_minutes", 5) * 60.0
    while not stop_event.is_set():
        if stop_event.wait(interval):
            break
        notifs = _maintenance_cycle(conn, config, write_lock)
        if not write_lock.acquire(timeout=2.0):
            continue
        try:
            _propagate(conn, config)
        finally:
            write_lock.release()
        with queue_lock:
            queue.extend(notifs)


if __name__ == "__main__":
    import anyio
    anyio.run(main)
