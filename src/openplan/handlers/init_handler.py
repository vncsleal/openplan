from __future__ import annotations

from mcp.types import CallToolResult

from openplan.core.state import init_project as _init
from openplan.handler_utils import _write_lock_acquire, _write_lock_release, _set_cursor, ok, get_conn, get_session_id, _push_resource_notification


async def handle_init(args: dict) -> CallToolResult:
    _write_lock_acquire()
    project = args["project"]
    try:
        result = _init(project, args.get("label"), get_conn(), session_id=get_session_id(), project_type=args.get("project_type", ""), goal=args.get("goal", ""))
        if result.get("state_id"):
            _set_cursor(project, result["state_id"])
    finally:
        _write_lock_release()
    if result.get("state_id"):
        await _push_resource_notification(project)
    return ok(result, project=project)
