from __future__ import annotations

from mcp.types import CallToolResult

from openplan.core.state import plan_project as _plan_project
from openplan.handler_utils import _write_lock_acquire, _write_lock_release, _set_cursor, ok, get_conn, get_session_id, _push_resource_notification


async def handle_start(args: dict) -> CallToolResult:
    _write_lock_acquire()
    project = args["project"]
    try:
        result = _plan_project(project, args["goal"], get_conn(), session_id=get_session_id(), project_type=args.get("project_type", ""), label=args.get("label"))
        if result.get("cursor"):
            _set_cursor(project, result["cursor"])
    finally:
        _write_lock_release()
    if result.get("state_id"):
        await _push_resource_notification(project)
    return ok(result, project=project)
