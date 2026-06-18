from __future__ import annotations

import json
import logging
import os
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP, Context
from mcp.server.session import ServerSession
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from openplan import VERSION
from openplan.db.connection import get_connection, close
from openplan.db.schema import init_db
from openplan.handlers import HANDLERS
from openplan.adapters.mesh import MeshAdapter

_log = logging.getLogger("openplan")


@dataclass
class AppContext:
    conn: Any
    config: dict
    mesh: MeshAdapter


def _load_config() -> dict:
    config_path = os.environ.get(
        "OPENPLAN_CONFIG",
        os.path.expanduser("~/.config/openplan/config.json"),
    )
    defaults = {
        "db_path": os.path.expanduser("~/.local/share/openplan/data.db"),
        "api_url": os.environ.get("OPENPLAN_API_URL", "https://api.openplan.cc"),
        "api_key": os.environ.get("OPENPLAN_API_KEY", ""),
        "cost_probe": None,
        "github_client_id": os.environ.get("OPENPLAN_GITHUB_CLIENT_ID", "Ov23lib55xjCggd9BIDy"),
        "github_client_secret": os.environ.get("OPENPLAN_GITHUB_CLIENT_SECRET", ""),
        "turso_url": os.environ.get("OPENPLAN_TURSO_URL", ""),
        "turso_token": os.environ.get("OPENPLAN_TURSO_TOKEN", ""),
        "stripe_product_id": os.environ.get("STRIPE_PRODUCT_ID", ""),
        "stripe_price_id": os.environ.get("STRIPE_PRICE_ID", ""),
        "stripe_webhook_secret": os.environ.get("STRIPE_WEBHOOK_SECRET", ""),
    }
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                user_config = json.load(f)
                defaults.update(user_config)
        except (json.JSONDecodeError, OSError):
            pass
    return defaults


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    config = _load_config()
    conn = get_connection(config["db_path"])
    init_db(conn)
    mesh = MeshAdapter(api_url=config.get("api_url", ""), api_key=config.get("api_key", ""))

    # Start background sync thread
    stop_event = asyncio.Event()
    sync_task = asyncio.create_task(_sync_loop(conn, mesh, stop_event))

    try:
        yield AppContext(conn=conn, config=config, mesh=mesh)
    finally:
        stop_event.set()
        sync_task.cancel()
        conn.commit()
        close()


async def _sync_loop(conn, mesh: MeshAdapter, stop_event: asyncio.Event):
    while not stop_event.is_set():
        try:
            mesh.sync_pending(conn)
            await mesh.pull_baselines()
        except Exception as e:
            _log.warning("Mesh sync failed: %s", e)
        await asyncio.sleep(300)


mcp = FastMCP(
    "OpenPlan",
    json_response=True,
    lifespan=app_lifespan,
    instructions="OpenPlan: Waze for AI agents. Plan projects with plan(), track progress with checkpoint(), review results with review().",
)


@mcp.tool(
    name="plan",
    description="Decompose a goal into a costed route with phases, estimates, and evidence.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def plan_tool(
    goal: str,
    context: str = "",
    replan: bool = False,
    ctx: Context[ServerSession, AppContext] | None = None,
) -> dict:
    """Plan a project from a goal description."""
    conn = ctx.request_context.lifespan_context.conn if ctx else get_connection()
    api_key = ctx.request_context.lifespan_context.config.get("api_key", "") if ctx else ""
    args = {"goal": goal, "context": context, "replan": replan, "api_key": api_key}
    return await HANDLERS["plan"](conn, args)


@mcp.tool(
    name="checkpoint",
    description="Record phase completion with cost, or get current route state (no args).",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
)
async def checkpoint_tool(
    phase: str | None = None,
    actual_cost: int | None = None,
    route_id: str | None = None,
    project: str | None = None,
    ctx: Context[ServerSession, AppContext] | None = None,
) -> dict:
    """Checkpoint a completed phase or get current state."""
    conn = ctx.request_context.lifespan_context.conn if ctx else get_connection()
    api_key = ctx.request_context.lifespan_context.config.get("api_key", "") if ctx else ""
    args = {
        "phase": phase,
        "actual_cost": actual_cost,
        "route_id": route_id,
        "project": project,
        "api_key": api_key,
    }
    return await HANDLERS["checkpoint"](conn, args)


@mcp.tool(
    name="review",
    description="Session retrospective — summary, deviations, learnings, self-diagnostics.",
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
)
async def review_tool(
    route_id: str | None = None,
    project: str | None = None,
    ctx: Context[ServerSession, AppContext] | None = None,
) -> dict:
    """Review a completed route or project."""
    conn = ctx.request_context.lifespan_context.conn if ctx else get_connection()
    api_key = ctx.request_context.lifespan_context.config.get("api_key", "") if ctx else ""
    args = {"route_id": route_id, "project": project, "api_key": api_key}
    return await HANDLERS["review"](conn, args)


@mcp.resource(uri="openplan://{project}/route", name="Route State", description="Current route with phase statuses", mime_type="application/json")
async def route_resource(project: str, ctx: Context[ServerSession, AppContext] | None = None) -> str:
    conn = ctx.request_context.lifespan_context.conn if ctx else get_connection()
    row = conn.execute(
        "SELECT id FROM routes WHERE project = ? AND archived = 0 ORDER BY created_at DESC LIMIT 1",
        (project,),
    ).fetchone()
    if not row:
        return json.dumps({"error": "no active route"})
    from openplan.core.tracker import get_route_status
    result = get_route_status(conn, row["id"])
    return json.dumps(result)


@mcp.resource(uri="openplan://profiles", name="Profile", description="Personal bias and accuracy stats", mime_type="application/json")
async def profiles_resource(ctx: Context[ServerSession, AppContext] | None = None) -> str:
    conn = ctx.request_context.lifespan_context.conn if ctx else get_connection()
    api_key = ctx.request_context.lifespan_context.config.get("api_key", "") if ctx else ""
    from openplan.core.costs import compute_personal_bias
    bias = compute_personal_bias(conn, api_key)
    total_checkpoints = conn.execute("SELECT COUNT(*) as cnt FROM calibration_events").fetchone()["cnt"]
    return json.dumps({"personal_bias": bias, "total_checkpoints": total_checkpoints})


@mcp.resource(uri="openplan://sync-status", name="Sync Status", description="Mesh sync health", mime_type="application/json")
async def sync_status_resource(ctx: Context[ServerSession, AppContext] | None = None) -> str:
    conn = ctx.request_context.lifespan_context.conn if ctx else get_connection()
    pending = conn.execute("SELECT COUNT(*) as cnt FROM calibration_events WHERE synced = 0").fetchone()["cnt"]
    return json.dumps({"pending_checkpoints": pending})


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
