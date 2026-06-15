from __future__ import annotations

from typing import Any

from mcp.types import CallToolResult

from openplan.core.graph import _graph_health
from openplan.core.planner import plan as _plan
from openplan.core.read import reconstruct as _reconstruct
from openplan.core.recommend import recommend as _recommend
from openplan.core.recommend import recommend_all as _recommend_all
from openplan.core.simulate import simulate as _simulate
from openplan.core.tree import build_tree as _tree
from openplan.handler_utils import _read_lock_acquire, _read_lock_release, ok, get_conn, get_config, _get_cursor


async def handle_recommend(args: dict) -> CallToolResult:
    _read_lock_acquire()
    try:
        conn = get_conn()
        project = args.get("project")
        query = args.get("query")
        target = args.get("target")
        raw_sequence = args.get("sequence")
        cursor = args.get("cursor")
        detail = args.get("detail", False)
        top_k = args.get("top_k")
        max_cost = args.get("max_cost")
        risk_adjustment = args.get("risk_adjustment")
        mode = args.get("mode")
        up_depth = args.get("up_depth", 0)
        fmt = args.get("format", "json")

        if mode == "plan":
            from openplan.core.estimator import estimate as _estimate
            result = _estimate(project or "", "", conn)
            result["mode"] = "plan"
            return ok(result)
        if mode == "retro":
            from openplan.core.retro import retro as _retro
            result = _retro(project, conn)
            if not result.get("ok"):
                return ok({**result, "project": project, "mode": "retro"})
            result["mode"] = "retro"
            return ok(result)
        if mode == "learnings":
            from openplan.core.learnings import learnings as _learnings
            result = _learnings(conn)
            result["mode"] = "learnings"
            return ok(result)

        if query:
            from openplan.core.graph import search as _search
            result = _search(query, conn, limit=top_k or 10)
            return ok(result)

        if target:
            result = _plan(project, target, conn, get_config(), detail=detail)
            return ok(result)

        if raw_sequence:
            parsed_seq: list[dict[str, Any]] = []
            for step in raw_sequence:
                if isinstance(step, dict):
                    parsed_seq.append({"action": step.get("action", "implement"), "target": step.get("target", "")})
            result = _simulate(project, parsed_seq, conn, get_config())
            return ok(result)

        if not project:
            results = _recommend_all(conn, get_config(), max_cost=max_cost)
            return ok({"ok": True, "results": results, "count": len(results), "project": project})

        cursor_val = cursor or _get_cursor(project)
        result = _recommend(project, conn, get_config(), detail=detail, cursor=cursor_val,
                           top_k=top_k, up_depth=up_depth, max_cost=max_cost,
                           risk_adjustment=risk_adjustment)

        if fmt == "ascii":
            tree_str = _tree(project, conn, cursor=cursor_val, up_depth=up_depth)
            result["tree"] = tree_str
            result["tree_format"] = "ascii"

        return ok(result)
    finally:
        _read_lock_release()
