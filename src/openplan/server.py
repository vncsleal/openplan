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
from openplan.core.graph import _graph_health
from openplan.core.insight_propagation import propagate as _propagate
from openplan.core.learning import tune as _tune
from openplan.core.maintenance import _run_cycle as _maintenance_cycle
from openplan.core.planner import plan as _plan
from openplan.core.read import reconstruct as _reconstruct
from openplan.core.read import update_state as _update_state
from openplan.core.recommend import recommend as _recommend
from openplan.core.recommend import recommend_all as _recommend_all
from openplan.core.state import act as _act
from openplan.core.state import abandon as _abandon
from openplan.core.state import init_project as _init
from openplan.core.state import branch as _branch
from openplan.core.export import prune as _prune
from openplan.core.simulate import simulate as _simulate
from openplan.core.tree import build_tree as _tree
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
        result = _init(project, args.get("label"), _get_conn(), session_id=_SESSION_ID, project_type=args.get("project_type", ""), goal=args.get("goal", ""))
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
    auto_tuned = False
    did_mutate = False
    try:
        conn = _get_conn()
        action = args.get("action", "implement")
        dry_run = args.get("dry_run", False)
        status = args.get("status")
        source = _get_cursor(project)
        if not source:
            root = conn.execute("SELECT id FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1", (project,)).fetchone()
            source = root["id"] if root else None
        if not source:
            return err("NO_CURSOR", f"No position in {project} — call init first")

        if action == "abandon":
            target_id = args.get("target") or source
            result = _abandon(target_id, conn, session_id=_SESSION_ID)
            need_notify = True
        elif action == "prune":
            target_id = args.get("target") or source
            result = _prune(target_id, conn, _config, summary_label=args.get("summary_label"), keep_events=args.get("keep_events", False), session_id=_SESSION_ID)
            need_notify = bool(result.get("ok"))
        elif action == "set_goal":
            goal = args.get("target", "")
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                         (f"goal:{project}", json.dumps({"text": goal, "target_state_id": None})))
            result = {"ok": True, "goal": goal}
        elif args.get("options"):
            result = _branch(source, args["options"], conn, _config, session_id=_SESSION_ID)
            created = result.get("states_created", [])
            if created:
                _set_cursor(project, created[0])
                result["next_state"] = created[0]
            need_notify = True
            did_mutate = True
        elif status or args.get("props_patch"):
            target_id = args.get("target") or source
            result = _update_state(target_id, conn, status=status, props_patch=args.get("props_patch"), session_id=_SESSION_ID)
            need_notify = True
        elif dry_run:
            from openplan.core.read import read_state as _read_state
            target_id = args.get("target") or source
            result = _read_state(target_id, conn)
        else:
            parent = args.get("parent")
            if parent:
                p_row = conn.execute("SELECT project FROM nodes WHERE id = ?", (parent,)).fetchone()
                if not p_row:
                    return err("INVALID_PARENT", f"Parent state {parent} not found")
                if p_row["project"] != project:
                    return err("PARENT_PROJECT_MISMATCH", f"Parent {parent} belongs to project '{p_row['project']}', not '{project}'")
                source = parent
            result = _act(source, action, conn, _config, target=args.get("target"),
                         evidence=args.get("evidence"), thought=args.get("thought"),
                         expected_cost=args.get("expected_cost"), actual_cost=args.get("actual_cost"),
                         session_id=_SESSION_ID, postconditions=args.get("postconditions"))
            need_notify = bool(result.get("next_state"))
            did_mutate = True
        if need_notify and result.get("next_state"):
            _set_cursor(project, result["next_state"])
        elif need_notify and result.get("state_id"):
            _set_cursor(project, result["state_id"])
        if did_mutate:
            tune_interval = _config.get("tune_interval", 10)
            if tune_interval > 0:
                count_row = _conn.execute("SELECT value FROM meta WHERE key = 'self_tuning:act_count'").fetchone()
                count = json.loads(count_row["value"]) if count_row else 0
                count += 1
                if count >= tune_interval:
                    _tune(conn, _config)
                    count = 0
                    auto_tuned = True
                _conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('self_tuning:act_count', ?)", (json.dumps(count),))
    finally:
        _write_lock_release()
    if need_notify:
        await _push_resource_notification(project)
    if auto_tuned:
        result["auto_tuned"] = True
    return ok(result, project=project)


async def _handle_recommend(args: dict) -> CallToolResult:
    _read_lock_acquire()
    try:
        conn = _get_conn()
        project = args.get("project")
        if not project or project == "*":
            results = _recommend_all(conn, _config, goal=args.get("goal"), max_cost=args.get("max_cost"))
            projects_data = [dict(r) for r in conn.execute(
                "SELECT n.project, MIN(n.id) AS root_id, COUNT(DISTINCT n2.id) AS state_count "
                "FROM nodes n LEFT JOIN nodes n2 ON n2.project = n.project "
                "GROUP BY n.project ORDER BY state_count DESC"
            ).fetchall()]
            return ok({"results": results, "count": len(results), "projects": projects_data})

        cursor = args.get("cursor") or _get_cursor(project)
        sequence = args.get("sequence")
        target = args.get("target")
        query = args.get("query")
        top_k = args.get("top_k")
        up_depth = args.get("up_depth")
        risk_adjustment = args.get("risk_adjustment")
        max_cost = args.get("max_cost")
        tree_format = args.get("format", "json")

        result: dict[str, Any] = {"ok": True}

        if query:
            from openplan.core.graph import search as _search
            sr = _search(query, conn, project=project, limit=top_k or 20)
            result.update(sr)
        elif sequence:
            sim_result = _simulate(project, sequence, conn, _config, cursor=cursor)
            result.update(sim_result)
            if sim_result.get("total_cost") is not None:
                result["expected_cost"] = {
                    "tokens": sim_result["total_cost"],
                    "prob": sim_result.get("cumulative_prob", 1.0),
                    "steps": sim_result.get("steps", 0),
                }
        elif target:
            constraints: dict[str, Any] = {}
            if top_k:
                constraints["top_k"] = top_k
            if risk_adjustment:
                constraints["risk_adjustment"] = risk_adjustment
            if max_cost:
                constraints["max_cost"] = max_cost
            plan_result = _plan(cursor, target, conn, _config, constraints=constraints or None, session_id=_SESSION_ID)
            result.update(plan_result)
        else:
            rec_result = _recommend(project, conn, _config, goal=args.get("goal"), max_cost=max_cost, cursor=cursor)
            result.update(rec_result)
            if top_k and rec_result.get("target"):
                try:
                    from openplan.core.read import compare_paths as _compare_paths
                    cp = _compare_paths(project, conn, [rec_result["target"]], config=_config, cursor=cursor)
                    if cp.get("results"):
                        result["alternatives"] = cp["results"]
                except Exception:
                    pass

        health = _graph_health(project, conn)
        result["project_health"] = {
            "total_states": health["state_count"],
            "edge_count": health["edge_count"],
            "max_depth": health["max_depth"],
            "orphan_count": health["orphan_count"],
            "calibration_count": health["calibration_count"],
            "completed": conn.execute("SELECT COUNT(*) AS cnt FROM nodes WHERE project = ? AND status = 'done'", (project,)).fetchone()["cnt"],
            "calibration_rate": round(health["calibration_count"] / health["edge_count"], 4) if health["edge_count"] > 0 else 0.0,
        }
        result["blockers"] = [dict(r) for r in conn.execute(
            "SELECT id, label, status FROM nodes WHERE project = ? AND status IN ('blocked', 'cascade_blocked')",
            (project,),
        ).fetchall()]

        goal_row = conn.execute("SELECT value FROM meta WHERE key = ?", (f"goal:{project}",)).fetchone()
        if goal_row:
            try:
                result["goal"] = json.loads(goal_row["value"])
            except (json.JSONDecodeError, TypeError):
                pass

        bandit_row = conn.execute("SELECT value FROM meta WHERE key = 'self_tuning:bandit'").fetchone()
        if bandit_row:
            try:
                bd = json.loads(bandit_row["value"])
                chosen = bd.get("chosen_arm")
                arms = bd.get("arms", {})
                if chosen and chosen in arms:
                    ab = arms[chosen]
                    alpha = ab.get("alpha", 1)
                    beta = ab.get("beta", 1)
                    tri = alpha + beta - 2
                    ar = alpha / (alpha + beta)
                    cv = "low_data" if tri < 5 else ("converging" if ar > 0.65 else "exploring")
                    cr = conn.execute("SELECT value FROM meta WHERE key='self_tuning:act_count'").fetchone()
                    ac = json.loads(cr["value"]) if cr else 0
                    ti = _config.get("tune_interval", 10)
                    result["self_tuning"] = {
                        "bandit_arm": chosen, "acceptance_rate": round(ar, 3),
                        "convergence": cv, "total_trials": tri,
                        "acts_since_tune": ac, "tune_due": ac >= ti,
                    }
            except (json.JSONDecodeError, TypeError, ZeroDivisionError):
                pass

        est_acc: dict[str, dict[str, float]] = {}
        for r in conn.execute("SELECT key, value FROM meta WHERE key LIKE 'tuning:%'"):
            action = r["key"][7:]
            try:
                td = json.loads(r["value"])
                if td.get("count", 0) > 1 and td.get("avg_cost", 0) > 0:
                    cv_raw = td.get("cost_stddev", 0)
                    est_acc[action] = {
                        "avg_cost": td["avg_cost"], "stddev": cv_raw,
                        "samples": td["count"], "ci": td.get("cost_ci_95"),
                    }
            except (json.JSONDecodeError, TypeError):
                pass
        result["estimation_accuracy"] = est_acc

        if query:
            pass
        elif target or (up_depth is not None and up_depth >= 0):
            tree_target = cursor if not target else target
            try:
                tr = _tree(state_id=tree_target, project=project if not tree_target else None,
                          conn=conn, depth=3, up_depth=up_depth or 0, fmt=tree_format)
                result["tree"] = tr.get("tree")
                result["tree_format"] = tree_format
            except Exception:
                pass

        if not sequence and not target and not query and not cursor:
            result["cursor"] = cursor

        return ok(result, project=project)
    finally:
        _read_lock_release()


HANDLERS = {
    "init": _handle_init,
    "act": _handle_act,
    "recommend": _handle_recommend,
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
        Resource(uri="openplan://tuning", name="Global Tuning", description="Per-action tuning statistics across all projects", mimeType="application/json"),
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
        resources.append(Resource(uri=f"openplan://{p}/edges", name=f"{p} Edges", description=f"All edges for {p}", mimeType="application/json"))
        resources.append(Resource(uri=f"openplan://{p}/health", name=f"{p} Health", description=f"Health snapshot for {p}", mimeType="application/json"))
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
    if uri == "openplan://tuning":
        tuning_data = {}
        for r in conn.execute("SELECT key, value FROM meta WHERE key LIKE 'tuning:%'"):
            try:
                tuning_data[r["key"][7:]] = json.loads(r["value"])
            except (json.JSONDecodeError, TypeError):
                pass
        return ReadResourceResult(contents=[TextResourceContents(uri=uri, text=json.dumps(tuning_data), mimeType="application/json")])
    m = re.match(r"openplan://(.+?)/graph", uri)
    if m:
        project = m.group(1)
        health = _graph_health(project, conn)
        return ReadResourceResult(contents=[TextResourceContents(uri=uri, text=json.dumps(health), mimeType="application/json")])
    m = re.match(r"openplan://(.+?)/edges", uri)
    if m:
        project = m.group(1)
        edges = [dict(r) for r in conn.execute(
            "SELECT e.*, src.label AS source_label, tgt.label AS target_label "
            "FROM edges e JOIN nodes src ON src.id = e.source_id JOIN nodes tgt ON tgt.id = e.target_id "
            "WHERE src.project = ?", (project,)
        ).fetchall()]
        return ReadResourceResult(contents=[TextResourceContents(uri=uri, text=json.dumps(edges), mimeType="application/json")])
    m = re.match(r"openplan://(.+?)/health", uri)
    if m:
        project = m.group(1)
        health = _graph_health(project, conn)
        return ReadResourceResult(contents=[TextResourceContents(uri=uri, text=json.dumps(health), mimeType="application/json")])
    m = re.match(r"openplan://(.+?)/states/(S-\d{6})$", uri)
    if m:
        project = m.group(1)
        state_id = m.group(2)
        node = conn.execute("SELECT * FROM nodes WHERE id = ? AND project = ?", (state_id, project)).fetchone()
        if node:
            return ReadResourceResult(contents=[TextResourceContents(uri=uri, text=json.dumps(dict(node)), mimeType="application/json")])
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
        Prompt(
            name="feature-plan",
            title="Feature Planning",
            description="Plan a new feature: recommend next target, simulate path, then act. Use when starting a new feature or work item.",
            arguments=[
                PromptArgument(name="project", description="Project slug", required=True),
                PromptArgument(name="feature", description="Description of the feature to plan", required=True),
            ],
        ),
        Prompt(
            name="debug-blocked",
            title="Debug Blocked State",
            description="Diagnose a blocked state: read its context, inspect the tree, find alternative paths. Use when a state is blocked or cascade_blocked.",
            arguments=[
                PromptArgument(name="project", description="Project slug", required=True),
                PromptArgument(name="state_id", description="ID of the blocked state", required=True),
            ],
        ),
        Prompt(
            name="review-progress",
            title="Review Project Progress",
            description="Audit project health: reconstruct the full picture, run tuning, check diagnostics. Use periodically or after completing a milestone.",
            arguments=[
                PromptArgument(name="project", description="Project slug", required=True),
            ],
        ),
    ]


@app.get_prompt()
async def get_prompt(name: str, arguments: dict[str, str] | None = None) -> GetPromptResult:
    project = arguments.get("project", "<project>") if arguments else "<project>"

    if name == "feature-plan":
        feature = arguments.get("feature", "the feature") if arguments else "the feature"
        return GetPromptResult(
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=f"""Plan the feature: {feature}

Project: {project}

1. Call recommend(project="{project}") to find the best next target
2. Call plan() to find the cheapest path to that target
3. Call simulate() to estimate total cost and probability
4. Call act() to execute the first step

Use the goal parameter if the project has a defined end state.""",
                    ),
                ),
            ],
        )

    if name == "debug-blocked":
        state_id = arguments.get("state_id", "<state_id>") if arguments else "<state_id>"
        return GetPromptResult(
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=f"""Debug blocked state {state_id} in project {project}

1. Call read_state(state_id="{state_id}") to understand the state
2. Call tree(state_id="{state_id}", up_depth=1) to see parent and siblings
3. Call plan() to find alternative paths around the block
4. If alternatives exist, call act() to unblock

Check if downstream states are cascade_blocked and need recovery.""",
                    ),
                ),
            ],
        )

    if name == "review-progress":
        return GetPromptResult(
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=f"""Review progress for project {project}

1. Call reconstruct(project="{project}") for the full picture
2. Call tune() for calibration statistics
3. Call diagnose() for system health issues

Check:
- Calibration rate (should be > 0.5)
- Orphan count (should not exceed 5 per 10 states)
- Blocked states and cascade_blocked propagation
- Self-tuning adjustments in meta:self_tuning:history""",
                    ),
                ),
            ],
        )

    return GetPromptResult(
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=f"""You have access to OpenPlan, an MCP server for AI-native project planning.

init(project, label, project_type?, goal?) — Create a new project context (idempotent). Set project_type for cost baselines (e.g. 'python_cli', 'web_app'). Set goal for the desired end state.
act(project, action, target, parent?, evidence?, thought?, expected_cost?, postconditions?) — Traverse to a target or create one. Auto-calibrates. Edge preconditions are validated automatically. Postconditions are stored on the target state.
recommend(project?, goal?, max_cost?, cursor?) — Find the best next target. When a goal is set, finds the cheapest A* path from cursor to goal-aligned states. Without a goal, uses activation scoring.
search(query?, project?, limit?) — Find projects, states, and insights.

Your current project is `{project}`.
Start with init (with optional goal), then act to create work items, recommend to find what to do next.""",
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
        _tune(_conn, _config)
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
                server_version="0.2.6",
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
