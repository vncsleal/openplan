from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from mcp.types import CallToolResult, TextContent

_log = logging.getLogger("openplan")

_conn: Any = None
_write_lock = threading.Lock()
_read_lock = threading.Lock()
_read_count = 0
_notification_queue: list[dict[str, Any]] = []
_notification_seen: set[str] = set()
_notification_lock = threading.Lock()
_config: dict[str, Any] = {}
_SESSION_ID: str = os.environ.get("OPENCODE_SESSION_ID", "")
_RESOURCE_PAGE_SIZE = 20


def set_conn(conn: Any) -> None:
    global _conn
    _conn = conn


def get_conn() -> Any:
    if _conn is None:
        raise RuntimeError("Database not initialized")
    return _conn


def set_config(config: dict[str, Any]) -> None:
    global _config
    _config = config


def get_config() -> dict[str, Any]:
    return _config


def set_session_id(sid: str) -> None:
    global _SESSION_ID
    _SESSION_ID = sid


def get_session_id() -> str:
    return _SESSION_ID


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


def _check_goal_markers(conn: Any, project: str, state_id: str, label: str, timestamp: str) -> None:
    conn.execute(
        "UPDATE goal_markers SET achieved = 1, achieved_at = ?, achieved_by = ? "
        "WHERE project = ? AND LOWER(criterion) = LOWER(?) AND achieved = 0",
        (timestamp, state_id, project, label),
    )


def _store_evidence(conn: Any, project: str, state_id: str, evidence_list: Any, timestamp: str) -> int:
    count = 0
    for ev in evidence_list if isinstance(evidence_list, list) else [evidence_list]:
        eid = str(uuid.uuid4())[:8]
        ev_type = ev.get("type", "checkpoint")
        ev_uri = ev.get("uri", "")
        ev_desc = ev.get("description", "")
        ev_status = "verified"
        metadata = "{}"
        if ev_type == "file" and ev_uri:
            try:
                st = os.stat(ev_uri)
                metadata = json.dumps({"size": st.st_size, "mtime": st.st_mtime})
            except OSError:
                ev_status = "unverified"
                metadata = json.dumps({"error": "file not found or inaccessible", "uri": ev_uri})
        conn.execute(
            "INSERT INTO evidence (id, project, state_id, evidence_type, uri, description, status, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, project, state_id, ev_type, ev_uri, ev_desc, ev_status, metadata, timestamp),
        )
        count += 1
    return count


async def _push_resource_notification(project: str) -> None:
    import anyio
    from mcp.types import ResourceUpdatedNotification, ResourceUpdatedNotificationParams
    try:
        async with anyio.create_task_group() as tg:
            tg.cancel_scope.cancel()
    except Exception:
        pass


def ok(data: dict[str, Any], project: str | None = None) -> CallToolResult:
    from openplan import VERSION
    enriched = dict(data)
    enriched["version"] = VERSION
    if project:
        notifs = _get_fresh_notifications(project)
        if notifs:
            enriched["_notifications"] = notifs
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(enriched))])


def err(code: str, message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"ok": False, "error": {"code": code, "message": message}}))],
        isError=True,
    )


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
