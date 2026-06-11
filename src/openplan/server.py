from __future__ import annotations

import atexit
import json
import logging
import os
import sqlite3
import threading
from typing import Any

_log = logging.getLogger("openplan")

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ServerCapabilities, TextContent, ToolsCapability

from openplan.config import load_config
from openplan.core.errors import OpenPlanError
from openplan.core.state import act as _act
from openplan.core.state import init_project as _init
from openplan.core.state import branch as _branch
from openplan.core.export import compress as _compress
from openplan.core.export import export as _export
from openplan.core.export import project_list as _project_list
from openplan.core.graph import diagnostics as _diagnostics
from openplan.core.graph import observe as _observe
from openplan.core.planner import learn as _learn
from openplan.core.planner import plan as _plan
from openplan.db.connection import get_connection
from openplan.db.schema import init_db
from openplan.tools.definitions import get_tools

_config: dict[str, Any] = {}
_conn: sqlite3.Connection | None = None
_write_lock = threading.Lock()
_read_lock = threading.Lock()
_read_count = 0
app = Server("openplan")


def _get_conn() -> sqlite3.Connection:
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


_SESSION_ID: str = os.environ.get("OPENCODE_SESSION_ID", "")
if not _SESSION_ID:
    _log.warning("OPENCODE_SESSION_ID not set — session tracking disabled")
    logging.basicConfig(level=logging.WARNING)


def ok(data: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"ok": True, "data": data}, default=_j))],
        structuredContent=data,
    )


def err(code: str, msg: str) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps({"ok": False, "error": {"code": code, "message": msg}}),
            )
        ],
        isError=True,
    )


def _j(o: Any) -> str:
    return o.hex() if isinstance(o, bytes) else str(o)


async def _handle_observe(args: dict) -> CallToolResult:
    _read_lock_acquire()
    try:
        result = _observe(
            args["project"],
            query=args.get("query"),
            scope=args.get("scope", "frontier"),
            conn=_get_conn(),
            config=_config,
            session_id=_SESSION_ID,
        )
        return ok(result)
    finally:
        _read_lock_release()


async def _handle_act(args: dict) -> CallToolResult:
    _write_lock_acquire()
    try:
        result = _act(
            args["state"], args["action"], _get_conn(), _config,
            target=args.get("target"), evidence=args.get("evidence"),
            thought=args.get("thought"), expected_cost=args.get("expected_cost"),
            session_id=_SESSION_ID,
        )
        return ok(result)
    finally:
        _write_lock_release()


async def _handle_export(args: dict) -> CallToolResult:
    _read_lock_acquire()
    try:
        result = _export(args["project"], conn=_get_conn(), fmt=args.get("format", "json"))
        return ok(result)
    finally:
        _read_lock_release()


async def _handle_branch(args: dict) -> CallToolResult:
    _write_lock_acquire()
    try:
        result = _branch(args["state"], args["options"], _get_conn(), _config, session_id=_SESSION_ID)
        return ok(result)
    finally:
        _write_lock_release()


async def _handle_plan(args: dict) -> CallToolResult:
    _read_lock_acquire()
    try:
        result = _plan(
            args["from_id"], args["target_id"], _get_conn(), _config,
            constraints=args.get("constraints"), session_id=_SESSION_ID,
        )
        return ok(result)
    finally:
        _read_lock_release()


async def _handle_learn(args: dict) -> CallToolResult:
    _write_lock_acquire()
    try:
        result = _learn(
            args["from_state"], args["to_state"], args["outcome"],
            args["actual_cost"], _get_conn(), _config,
            insight=args.get("insight", ""), session_id=_SESSION_ID,
        )
        return ok(result)
    finally:
        _write_lock_release()


async def _handle_init(args: dict) -> CallToolResult:
    _write_lock_acquire()
    try:
        result = _init(args["project"], args.get("label"), _get_conn(), session_id=_SESSION_ID)
        return ok(result)
    finally:
        _write_lock_release()


async def _handle_diagnostics(args: dict) -> CallToolResult:
    _read_lock_acquire()
    try:
        result = _diagnostics(
            args["project"],
            _get_conn(),
        )
        return ok(result)
    finally:
        _read_lock_release()


async def _handle_project_list(args: dict) -> CallToolResult:
    _read_lock_acquire()
    try:
        result = _project_list(_get_conn())
        return ok(result)
    finally:
        _read_lock_release()


async def _handle_compress(args: dict) -> CallToolResult:
    _write_lock_acquire()
    try:
        result = _compress(
            args["project"], _get_conn(), _config,
            older_than_days=args.get("older_than_days", 30),
            merge_orphans=args.get("merge_orphans", True),
            session_id=_SESSION_ID,
        )
        return ok(result)
    finally:
        _write_lock_release()


HANDLERS = {
    "observe": _handle_observe,
    "act": _handle_act,
    "export": _handle_export,
    "branch": _handle_branch,
    "plan": _handle_plan,
    "learn": _handle_learn,
    "init": _handle_init,
    "diagnostics": _handle_diagnostics,
    "project_list": _handle_project_list,
    "compress": _handle_compress,
}


@app.list_tools()
async def list_tools() -> list:
    return get_tools()


def _shutdown() -> None:
    global _conn
    from openplan.core.embedding import shutdown_embeddings
    shutdown_embeddings()
    if _conn is not None:
        _conn.close()


async def main() -> None:
    global _config, _conn
    _config = load_config()
    _conn = get_connection(_config.get("db_path", "openplan.db"))
    init_db(_conn)
    atexit.register(_shutdown)

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
                server_version="0.1.0",
                capabilities=ServerCapabilities(tools=ToolsCapability(listChanged=True)),
            ),
        )


if __name__ == "__main__":
    import anyio

    anyio.run(main)
