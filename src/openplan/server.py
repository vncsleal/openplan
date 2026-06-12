from __future__ import annotations

import atexit
import json
import logging
import os
import re
import threading
import time
import uuid
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
        result = _init(project, args.get("label"), _get_conn(), session_id=_SESSION_ID)
        if result.get("state_id"):
            _set_cursor(project, result["state_id"])
    finally:
        _write_lock_release()
    if result.get("state_id"):
        await _push_resource_notification(project)
    return ok(result, project=project)


async def _handle_act(args: dict) -> CallToolResult:
    project = args["project"]
    _write_lock_acquire()
    need_notify = False
    try:
        conn = _get_conn()
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
        need_notify = bool(result.get("next_state"))
        if need_notify:
            _set_cursor(project, result["next_state"])
    finally:
        _write_lock_release()
    if need_notify:
        await _push_resource_notification(project)
    return ok(result, project=project)


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
        project = args.get("project")
        if not query:
            if project:
                projects_data = [dict(r) for r in conn.execute(
                    "SELECT n.project, MIN(n.id) AS root_id, "
                    "(SELECT label FROM nodes WHERE project = n.project ORDER BY id LIMIT 1) AS root_label, "
                    "COUNT(DISTINCT n2.id) AS state_count FROM nodes n "
                    "LEFT JOIN nodes n2 ON n2.project = n.project "
                    "WHERE n.project = ? "
                    "GROUP BY n.project",
                    (project,),
                ).fetchall()]
            else:
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
        limit = args.get("limit", 20)
        result = _search(query, conn, project=project, limit=limit)
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
async def list_resources(req: Any = None) -> ListResourcesResult:
    conn = _get_conn()
    cursor = req.params.cursor if req and hasattr(req, "params") and req.params else None
    resources: list[Resource] = [
        Resource(uri="openplan://projects", name="All Projects", description="All projects with state counts", mimeType="application/json"),
        Resource(uri="openplan://analytics", name="Analytics", description="Cross-project analytics and anomaly detection", mimeType="application/json"),
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
    m = re.match(r"openplan://(.+?)/graph", uri)
    if m:
        project = m.group(1)
        health = _graph_health(project, conn)
        return ReadResourceResult(contents=[TextResourceContents(uri=uri, text=json.dumps(health), mimeType="application/json")])
    return ReadResourceResult(contents=[TextResourceContents(uri=uri, text=json.dumps({"error": "not found"}), mimeType="application/json")])


@app.list_prompts()
async def list_prompts() -> list[Prompt]:
    return [
        Prompt(
            name="agent_loop",
            title="Agent Loop",
            description="The recommended workflow for using OpenPlan: init → act → recommend → search",
            arguments=[
                PromptArgument(name="project", description="Project slug", required=False),
            ],
        ),
    ]


@app.get_prompt()
async def get_prompt(name: str, arguments: dict[str, str] | None = None) -> GetPromptResult:
    project = arguments.get("project", "<project>") if arguments else "<project>"
    return GetPromptResult(
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=f"""You have access to OpenPlan, an MCP server with 4 tools:

init(project, label) — Create a new project context (idempotent).
act(project, action, target, parent?, evidence?, thought?, expected_cost?) — Traverse to a target or create one. Auto-calibrates. Use `parent` to create siblings.
recommend(project?, goal?, max_cost?, cursor?) — Find the best next target with an A* plan. Omit project for cross-project.
search(query?) — Find projects, states, and insights across everything. Omit query for full project index.

Your current project is `{project}`.
Start with init if it doesn't exist, then act to create work items, recommend to find what to do next, and search to find relevant knowledge.""",
                ),
            ),
        ],
    )


def _shutdown() -> None:
    global _conn, _maintenance_stop
    _maintenance_stop.set()
    _telemetry.flush_to_events()
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
    atexit.register(_shutdown)

    notifs = _maintenance_cycle(_conn, _config, _write_lock)
    with _notification_lock:
        _notification_queue.extend(notifs)
    _write_lock_acquire()
    try:
        _propagate(_conn, _config)
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
                server_version="0.2.1",
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
