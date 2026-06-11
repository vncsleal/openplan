from __future__ import annotations

import atexit
import json
import logging
import os
import re
import threading
from typing import Any

_log = logging.getLogger("openplan")

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, Resource, ReadResourceContents, ServerCapabilities, TextContent, ToolsCapability, ResourcesCapability

from openplan.config import load_config
from openplan.core.analytics import compute_analytics
from openplan.core.errors import OpenPlanError
from openplan.core.graph import _graph_health, search as _search
from openplan.core.insight_propagation import propagate as _propagate
from openplan.core.maintenance import _run_cycle as _maintenance_cycle
from openplan.core.recommend import recommend as _recommend
from openplan.core.recommend import recommend_all as _recommend_all
from openplan.core.state import act as _act
from openplan.core.state import init_project as _init
from openplan.core.telemetry import get_telemetry
from openplan.db.connection import get_connection
from openplan.db.schema import init_db
from openplan.tools.definitions import get_tools

_config: dict[str, Any] = {}
_conn: Any = None
_write_lock = threading.Lock()
_read_lock = threading.Lock()
_read_count = 0
_notification_queue: list[dict[str, Any]] = []
_notification_seen: set[str] = set()
_telemetry = get_telemetry()
_SESSION_ID: str = os.environ.get("OPENCODE_SESSION_ID", "")
if not _SESSION_ID:
    _log.warning("OPENCODE_SESSION_ID not set — session tracking disabled")
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


def _get_cursor(project: str) -> str | None:
    if _conn is None:
        return None
    row = _conn.execute("SELECT cursor_state_id FROM sessions WHERE session_id = ? AND project = ?", (_SESSION_ID, project)).fetchone()
    if row:
        return row["cursor_state_id"]
    return None


def _set_cursor(project: str, state_id: str) -> None:
    if _conn is None:
        return
    _conn.execute(
        "INSERT OR REPLACE INTO sessions (session_id, project, cursor_state_id, created_at, updated_at) VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
        (_SESSION_ID, project, state_id),
    )


def _notif_hash(n: dict) -> str:
    return f"{n.get('code', '')}:{n.get('project', '')}"


def _get_fresh_notifications(project: str | None = None) -> list[dict]:
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


def _push_resource_notification(project: str) -> None:
    _notification_queue.append({"type": "resource_updated", "uri": f"openplan://{project}/graph"})


async def _handle_init(args: dict) -> CallToolResult:
    _write_lock_acquire()
    try:
        project = args["project"]
        result = _init(project, args.get("label"), _get_conn(), session_id=_SESSION_ID)
        if result.get("state_id"):
            _set_cursor(project, result["state_id"])
            _push_resource_notification(project)
        return ok(result, project=project)
    finally:
        _write_lock_release()


async def _handle_act(args: dict) -> CallToolResult:
    _write_lock_acquire()
    try:
        conn = _get_conn()
        project = args["project"]
        source = _get_cursor(project)
        if not source:
            root = conn.execute("SELECT id FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1", (project,)).fetchone()
            source = root["id"] if root else None
        if not source:
            return err("NO_CURSOR", f"No position in {project} — call init first")
        parent = args.get("parent")
        if parent:
            p_row = conn.execute("SELECT project FROM nodes WHERE id = ?", (parent,)).fetchone()
            if not p_row:
                return err("INVALID_PARENT", f"Parent state {parent} not found")
            if p_row["project"] != project:
                return err("PARENT_PROJECT_MISMATCH", f"Parent {parent} belongs to project '{p_row['project']}', not '{project}'")
            source = parent
        result = _act(source, args["action"], conn, _config, target=args.get("target"), evidence=args.get("evidence"), thought=args.get("thought"), expected_cost=args.get("expected_cost"), session_id=_SESSION_ID)
        if result.get("next_state"):
            _set_cursor(project, result["next_state"])
            _push_resource_notification(project)
        return ok(result, project=project)
    finally:
        _write_lock_release()


async def _handle_recommend(args: dict) -> CallToolResult:
    _read_lock_acquire()
    try:
        conn = _get_conn()
        project = args.get("project")
        if not project or project == "*":
            results = _recommend_all(conn, _config, goal=args.get("goal"), max_cost=args.get("max_cost"))
            projects = [dict(r) for r in conn.execute(
                "SELECT n.project, MIN(n.id) AS root_id, COUNT(DISTINCT n2.id) AS state_count "
                "FROM nodes n LEFT JOIN nodes n2 ON n2.project = n.project "
                "GROUP BY n.project ORDER BY state_count DESC"
            ).fetchall()]
            return ok({"results": results, "count": len(results), "projects": projects})
        cursor = args.get("cursor") or _get_cursor(project)
        result = _recommend(project, conn, _config, goal=args.get("goal"), max_cost=args.get("max_cost"), cursor=cursor)
        return ok(result, project=project)
    finally:
        _read_lock_release()


async def _handle_search(args: dict) -> CallToolResult:
    _read_lock_acquire()
    try:
        conn = _get_conn()
        query = args.get("query")
        if not query:
            projects_data = [dict(r) for r in conn.execute(
                "SELECT n.project, MIN(n.id) AS root_id, "
                "(SELECT label FROM nodes WHERE project = n.project ORDER BY id LIMIT 1) AS root_label, "
                "COUNT(DISTINCT n2.id) AS state_count FROM nodes n "
                "LEFT JOIN nodes n2 ON n2.project = n.project "
                "GROUP BY n.project ORDER BY state_count DESC"
            ).fetchall()]
            conv = _telemetry.get_global_conversion_rate()
            result: dict[str, Any] = {"query": None, "projects": projects_data, "count": len(projects_data)}
            if conv is not None:
                result["telemetry"] = {"global_conversion_rate": conv}
            return ok(result)
        result = _search(query, conn)
        return ok(result)
    finally:
        _read_lock_release()


HANDLERS = {
    "init": _handle_init,
    "act": _handle_act,
    "recommend": _handle_recommend,
    "search": _handle_search,
}


@app.list_tools()
async def list_tools() -> list:
    return get_tools()


@app.list_resources()
async def list_resources() -> list[Resource]:
    conn = _get_conn()
    resources = [Resource(uri="openplan://projects", name="All Projects", description="All projects with state counts", mimeType="application/json")]
    resources.append(Resource(uri="openplan://analytics", name="Analytics", description="Cross-project analytics and anomaly detection", mimeType="application/json"))
    for row in conn.execute("SELECT DISTINCT project FROM nodes").fetchall():
        p = row["project"]
        resources.append(Resource(uri=f"openplan://{p}/graph", name=f"{p} Graph", description=f"Full graph for {p}", mimeType="application/json"))
    return resources


@app.read_resource()
async def read_resource(uri: str) -> list[ReadResourceContents]:
    conn = _get_conn()
    if uri == "openplan://projects":
        projects = [dict(r) for r in conn.execute("SELECT DISTINCT project, COUNT(*) AS state_count FROM nodes GROUP BY project").fetchall()]
        return [ReadResourceContents(content=json.dumps(projects), mimeType="application/json")]
    if uri == "openplan://analytics":
        return [ReadResourceContents(content=json.dumps(compute_analytics(conn)), mimeType="application/json")]
    m = re.match(r"openplan://(.+?)/graph", uri)
    if m:
        project = m.group(1)
        health = _graph_health(project, conn)
        return [ReadResourceContents(content=json.dumps(health), mimeType="application/json")]
    return [ReadResourceContents(content=json.dumps({"error": "not found"}), mimeType="application/json")]


def _shutdown() -> None:
    global _conn
    _telemetry.flush_to_events()
    if _conn is not None:
        _conn.close()


async def main() -> None:
    global _config, _conn
    _config = load_config()
    _conn = get_connection(_config.get("db_path", "openplan.db"))
    init_db(_conn)
    _telemetry.set_conn(_conn)
    _telemetry.reload_from_events()
    atexit.register(_shutdown)

    notifs = _maintenance_cycle(_conn, _config, _write_lock)
    _notification_queue.extend(notifs)

    _propagate(_conn, _config)

    maintenance_thread = threading.Thread(
        target=_maintenance_loop,
        args=(_conn, _config, _write_lock, _notification_queue),
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
                server_version="0.2.0",
                capabilities=ServerCapabilities(tools=ToolsCapability(listChanged=True), resources=ResourcesCapability(listChanged=True, subscribe=True)),
            ),
        )


def _maintenance_loop(conn: Any, config: dict, write_lock: threading.Lock, queue: list) -> None:
    interval = config.get("maintenance_interval_minutes", 5) * 60.0
    while True:
        import time
        time.sleep(interval)
        notifs = _maintenance_cycle(conn, config, write_lock)
        _propagate(conn, config)
        queue.extend(notifs)


if __name__ == "__main__":
    import anyio
    anyio.run(main)
