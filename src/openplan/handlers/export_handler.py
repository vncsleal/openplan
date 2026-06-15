from __future__ import annotations

from mcp.types import CallToolResult

from openplan.core.export import export as _export
from openplan.handler_utils import _read_lock_acquire, _read_lock_release, ok, get_conn


async def handle_export(args: dict) -> CallToolResult:
    _read_lock_acquire()
    try:
        conn = get_conn()
        project = args["project"]
        fmt = args.get("format", "json")
        result = _export(project, conn, fmt=fmt)
        result["ok"] = True
        return ok(result, project=project)
    finally:
        _read_lock_release()
