from __future__ import annotations

import atexit
import json
import logging
import os
import threading
from typing import Any

_log = logging.getLogger("openplan")

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ServerCapabilities, TextContent, ToolsCapability

from openplan.config import load_config
from openplan.core.errors import OpenPlanError
from openplan.core.export import compress as _compress
from openplan.core.graph import search as _search
from openplan.core.graph import diagnostics as _diagnostics
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
_last_cursor: dict[str, str] = {}
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


def ok(data: dict[str, Any]) -> CallToolResult:
    result_str = json.dumps({"ok": True, "data": data, **({"_notifications": list(_notification_queue)} if _notification_queue else {})}, default=_j)
    _notification_queue.clear()
    return CallToolResult(
        content=[TextContent(type="text", text=result_str)],
        structuredContent=data,
    )


def err(code: str, msg: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"ok": False, "error": {"code": code, "message": msg}}))],
        isError=True,
    )


def _j(o: Any) -> str:
    return o.hex() if isinstance(o, bytes) else str(o)


def _get_cursor(project: str) -> str | None:
    return _last_cursor.get(project)


def _set_cursor(project: str, state_id: str) -> None:
    _last_cursor[project] = state_id


async def _handle_init(args: dict) -> CallToolResult:
    _write_lock_acquire()
    try:
        project = args["project"]
        result = _init(project, args.get("label"), _get_conn(), session_id=_SESSION_ID)
        if result.get("state_id"):
            _set_cursor(project, result["state_id"])
        return ok(result)
    finally:
        _write_lock_release()


async def _handle_act(args: dict) -> CallToolResult:
    _write_lock_acquire()
    try:
        conn = _get_conn()
        project = args["project"]
        cursor = _get_cursor(project)
        if not cursor:
            root = conn.execute("SELECT id FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1", (project,)).fetchone()
            cursor = root["id"] if root else None
        if not cursor:
            return err("NO_CURSOR", f"No position in {project} — call init first")
        result = _act(cursor, args["action"], conn, _config, target=args.get("target"), evidence=args.get("evidence"), thought=args.get("thought"), expected_cost=args.get("expected_cost"), session_id=_SESSION_ID)
        if result.get("next_state"):
            _set_cursor(project, result["next_state"])
        return ok(result)
    finally:
        _write_lock_release()


async def _handle_recommend(args: dict) -> CallToolResult:
    _read_lock_acquire()
    try:
        project = args.get("project")
        if not project or project == "*":
            results = _recommend_all(_get_conn(), _config, goal=args.get("goal"), max_cost=args.get("max_cost"))
            return ok({"results": results, "count": len(results)})
        cursor = args.get("cursor") or _get_cursor(project)
        result = _recommend(project, _get_conn(), _config, goal=args.get("goal"), max_cost=args.get("max_cost"), cursor=cursor)
        return ok(result)
    finally:
        _read_lock_release()


async def _handle_search(args: dict) -> CallToolResult:
    _read_lock_acquire()
    try:
        result = _search(args["query"], _get_conn())
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
            result = await handler(arguments)
            if _notification_queue:
                if hasattr(result, "content") and result.content:
                    import copy
                    txt = result.content[0].text
                    parsed = json.loads(txt)
                    parsed.setdefault("_notifications", []).extend(_notification_queue)
                    _notification_queue.clear()
                    result.content[0].text = json.dumps(parsed)
            return result
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
                server_version="0.1.7",
                capabilities=ServerCapabilities(tools=ToolsCapability(listChanged=True)),
            ),
        )


def _maintenance_loop(conn: Any, config: dict, write_lock: threading.Lock, queue: list) -> None:
    interval = config.get("maintenance_interval_minutes", 5) * 60.0
    while True:
        import time
        time.sleep(interval)
        notifs = _maintenance_cycle(conn, config, write_lock)
        queue.extend(notifs)


if __name__ == "__main__":
    import anyio
    anyio.run(main)
