from __future__ import annotations

import atexit
import json
import logging
import os
import threading
import time
from typing import Any

_log = logging.getLogger("openplan")

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    GetPromptResult, ListResourcesResult, Prompt,
    PromptMessage, PromptArgument, ReadResourceResult, Resource,
    ServerCapabilities, ServerNotification, TextContent, TextResourceContents,
    ToolsCapability, ResourcesCapability,
)

from openplan import VERSION
from openplan.config import load_config
from openplan.core.analytics import compute_analytics
from openplan.core.errors import OpenPlanError
from openplan.core.insight_propagation import propagate as _propagate
from openplan.core.learning import tune as _tune
from openplan.core.maintenance import _run_cycle as _maintenance_cycle
from openplan.core.telemetry import get_telemetry
from openplan.core.telemetry import ensure_schema as _ensure_telemetry_schema
from openplan.core.telemetry import import_global_calibration as _import_calibration
from openplan.core.telemetry import sync_to_endpoint as _sync_telemetry
from openplan.db.connection import get_connection
from openplan.db.schema import init_db
from openplan.handler_utils import (
    _config, _conn, _notification_queue, _notification_lock, _notification_seen,
    _read_lock_acquire, _read_lock_release, _SESSION_ID,
    _write_lock, _write_lock_acquire, _write_lock_release,
    get_conn, get_config, get_session_id, ok, err,
    set_config, set_conn, set_session_id, _resolve_session_id,
)
from openplan.handlers import HANDLERS
from openplan.tools.definitions import get_tools

from pydantic import AnyUrl

_app = Server("openplan")
_CONN: Any = None
_maintenance_stop = threading.Event()
_telemetry = get_telemetry()


def _shutdown() -> None:
    global _CONN, _maintenance_stop
    _maintenance_stop.set()
    _telemetry.flush_to_events()
    if _CONN is not None:
        endpoint = get_config().get("api_url", "") or os.environ.get("OPENPLAN_API_URL", "")
        if endpoint:
            try:
                _sync_telemetry(_CONN, endpoint)
            except Exception:
                pass
        _CONN.close()


def _resolve_goal(project: str) -> str:
    conn = get_conn()
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (f"goal:{project}",)).fetchone()
    if row:
        try:
            gv = json.loads(row["value"])
            return gv.get("text", "") if isinstance(gv, dict) else ""
        except (json.JSONDecodeError, TypeError):
            pass
    return ""


def _resolve_project_type(project: str) -> str:
    conn = get_conn()
    row = conn.execute(
        "SELECT project_type FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
        (project,),
    ).fetchone()
    return row["project_type"] or "" if row else ""


@_app.list_tools()
async def list_tools() -> list:
    return get_tools()


@_app.list_prompts()
async def list_prompts() -> list[Prompt]:
    return [
        Prompt(
            name="agent_loop",
            description="Full agent loop: init → branch → act → verify, with telemetry and calibration",
            arguments=[PromptArgument(name="project", description="Project slug", required=True)],
        ),
        Prompt(
            name="feature-plan",
            description="Plan a new feature: explain the change, break into states, add estimates",
            arguments=[PromptArgument(name="project", description="Project slug", required=True)],
        ),
    ]


@_app.get_prompt()
async def get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
    if name == "agent_loop":
        project = arguments.get("project", "") if arguments else ""
        return GetPromptResult(
            description=f"Agent loop for {project}",
            messages=[
                PromptMessage(role="user", content=TextContent(
                    type="text",
                    text=f"""You are working on project '{project}'. Use OpenPlan to track progress.

Commands:
start(project, goal, project_type?) — Create project with phases and estimates.
  project_type helps calibration: python_cli, typescript_library, rust_library, web_app.
  Set goal for the desired end state (comma-separated for multiple markers).

complete(project, state, evidence?, actual_cost?, auto_verify?) — Complete a phase.
  Auto-traverses to the next phase and returns updated plan health.

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
  start(project_type=...) → complete × N → recommend
  When done: act(action="verify", satisfies_goal="criterion") to tick goal markers.
  Use recommend(query=...) to search past decisions.
  Use export(format="graphml") to visualize the graph externally.

Best practices (learned from self-hosting):
  1. ALWAYS set project_type on start — otherwise your work is invisible to cross-project estimation.
  2. Use satisfies_goal on verify to explicitly tick markers — subtitle matching is fragile.
  3. Pass expected_cost on traverse to get meaningful cost_delta and calibration.""",
                )),
            ],
        )
    return GetPromptResult(description="", messages=[])


@_app.list_resources()
async def list_resources(req: Any = None) -> ListResourcesResult:
    conn = get_conn()
    cursor = req.params.cursor if req and hasattr(req, "params") and req.params else None
    resources: list[Resource] = [
        Resource(uri="openplan://projects", name="All Projects", description="All projects with state counts", mimeType="application/json"),
        Resource(uri="openplan://analytics", name="Analytics", description="Cross-project analytics and anomaly detection", mimeType="application/json"),
        Resource(uri="openplan://tuning", name="Global Tuning", description="Per-action tuning statistics across all projects", mimeType="application/json"),
    ]
    page_size = 20
    if cursor:
        project_rows = conn.execute(
            "SELECT DISTINCT project FROM nodes WHERE project > ? ORDER BY project LIMIT ?",
            (cursor, page_size),
        ).fetchall()
    else:
        project_rows = conn.execute(
            "SELECT DISTINCT project FROM nodes ORDER BY project LIMIT ?",
            (page_size,),
        ).fetchall()
    for row in project_rows:
        p = row["project"]
        resources.append(Resource(uri=f"openplan://{p}/graph", name=f"{p} Graph", description=f"Full graph for {p}", mimeType="application/json"))
        resources.append(Resource(uri=f"openplan://{p}/edges", name=f"{p} Edges", description=f"All edges for {p}", mimeType="application/json"))
        resources.append(Resource(uri=f"openplan://{p}/health", name=f"{p} Health", description=f"Health snapshot for {p}", mimeType="application/json"))
    next_cursor = project_rows[-1]["project"] if len(project_rows) == page_size else None
    return ListResourcesResult(resources=resources, nextCursor=next_cursor)


@_app.read_resource()
async def read_resource(uri: str) -> ReadResourceResult:
    conn = get_conn()
    if uri == "openplan://projects":
        rows = conn.execute("SELECT project, COUNT(*) AS cnt FROM nodes GROUP BY project ORDER BY project").fetchall()
        return ReadResourceResult(contents=[TextResourceContents(uri=uri, mimeType="application/json", text=json.dumps([dict(r) for r in rows]))])
    if uri == "openplan://analytics":
        data = compute_analytics(conn)
        return ReadResourceResult(contents=[TextResourceContents(uri=uri, mimeType="application/json", text=json.dumps(data))])
    if uri == "openplan://tuning":
        tuning: dict[str, Any] = {}
        for r in conn.execute("SELECT key, value FROM meta WHERE key LIKE 'tuning:%'"):
            key = r["key"][7:]
            try:
                tuning[key] = json.loads(r["value"])
            except (json.JSONDecodeError, TypeError):
                pass
        return ReadResourceResult(contents=[TextResourceContents(uri=uri, mimeType="application/json", text=json.dumps(tuning))])
    from openplan.core.graph import _graph_health as _gh
    from openplan.core.export import export as _export
    if uri.endswith("/graph"):
        project = uri.split("/")[-2]
        data = _export(project, conn)
        return ReadResourceResult(contents=[TextResourceContents(uri=uri, mimeType="application/json", text=json.dumps(data))])
    if uri.endswith("/edges"):
        project = uri.split("/")[-2]
        edges = [dict(r) for r in conn.execute("SELECT e.* FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ? ORDER BY e.source_id", (project,)).fetchall()]
        return ReadResourceResult(contents=[TextResourceContents(uri=uri, mimeType="application/json", text=json.dumps(edges))])
    if uri.endswith("/health"):
        project = uri.split("/")[-2]
        health = _gh(project, conn)
        return ReadResourceResult(contents=[TextResourceContents(uri=uri, mimeType="application/json", text=json.dumps(health))])
    return ReadResourceResult(contents=[TextResourceContents(uri=uri, mimeType="text/plain", text=f"Resource not found: {uri}")])


@_app.call_tool()
async def call_tool(name: str, arguments: dict) -> CallToolResult:
    from mcp.types import CallToolResult as _CTR, TextContent as _TC
    handler = HANDLERS.get(name)
    if not handler:
        return _CTR(content=[_TC(type="text", text=json.dumps({"ok": False, "error": {"code": "UNKNOWN", "message": f"Unknown tool: {name}"}}))], isError=True)
    try:
        result = await handler(arguments)
        if isinstance(result, dict):
            text = json.dumps(result)
            return _CTR(content=[_TC(type="text", text=text)], structuredContent=result)
        return result
    except OpenPlanError as e:
        err_data = {"ok": False, "error": {"code": e.code, "message": e.message}}
        return _CTR(content=[_TC(type="text", text=json.dumps(err_data))], structuredContent=err_data, isError=True)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        err_data = {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": str(e)}}
        return _CTR(content=[_TC(type="text", text=json.dumps(err_data))], structuredContent=err_data, isError=True)


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
            endpoint = config.get("api_url", "") or os.environ.get("OPENPLAN_API_URL", "")
            if endpoint:
                _sync_telemetry(conn, endpoint)
        finally:
            write_lock.release()
        with queue_lock:
            queue.extend(notifs)


async def main() -> None:
    global _config, _conn, _SESSION_ID, _CONN
    config = load_config()
    set_config(config)
    conn = get_connection(config.get("db_path", "openplan.db"))
    set_conn(conn)
    _CONN = conn
    conn = get_conn()
    init_db(conn)
    session_id = f"{_SESSION_ID}:{os.environ.get('OPENCODE_SESSION_ID', '')}" if _SESSION_ID else os.environ.get("OPENCODE_SESSION_ID", "")
    if not session_id:
        session_id = _resolve_session_id(conn)
    set_session_id(session_id)
    _telemetry.set_conn(conn)
    _telemetry.reload_from_events()
    _ensure_telemetry_schema(conn)

    api_url = config.get("api_url", "") or os.environ.get("OPENPLAN_API_URL", "")
    if api_url:
        imported = _import_calibration(conn, api_url)
        if imported:
            _log.info("Telemetry: imported %d global calibration baselines from %s", imported, api_url)

    atexit.register(_shutdown)

    notifs = _maintenance_cycle(conn, config, _write_lock)
    with _notification_lock:
        _notification_queue.extend(notifs)
    _write_lock_acquire()
    try:
        _propagate(conn, config)
        _tune(conn, config)
    finally:
        _write_lock_release()

    maintenance_thread = threading.Thread(
        target=_maintenance_loop,
        args=(conn, config, _write_lock, _notification_queue, _notification_lock, _maintenance_stop),
        daemon=True,
    )
    maintenance_thread.start()

    from openplan.core.embedding import warmup_embeddings
    warmup_embeddings()

    async with stdio_server() as (read, write):
        await _app.run(
            read,
            write,
            InitializationOptions(
                server_name="openplan",
                server_version=VERSION,
                capabilities=ServerCapabilities(tools=ToolsCapability(listChanged=True), resources=ResourcesCapability(listChanged=True, subscribe=True)),
            ),
        )


if __name__ == "__main__":
    import anyio
    anyio.run(main)
