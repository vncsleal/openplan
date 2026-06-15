from __future__ import annotations

import json
from typing import Any

from openplan.core.graph import _graph_health
from openplan.core.planner import plan as _plan
from openplan.core.read import reconstruct as _reconstruct
from openplan.core.recommend import recommend as _recommend
from openplan.core.recommend import recommend_all as _recommend_all
from openplan.core.simulate import simulate as _simulate
from openplan.core.tree import build_tree as _tree
from openplan.handler_utils import _read_lock_acquire, _read_lock_release, ok, get_conn, get_config, _get_cursor


def _enrich_detail(result: dict[str, Any], conn: Any) -> None:
    tuning: dict[str, Any] = {}
    for r in conn.execute("SELECT key, value FROM meta WHERE key LIKE 'tuning:%'"):
        key = r["key"][7:]
        try:
            tuning[key] = json.loads(r["value"])
        except (json.JSONDecodeError, TypeError):
            pass
    if tuning:
        result["self_tuning"] = tuning.get("self_tuning", tuning)

    estimation_by_type: dict[str, dict[str, Any]] = {}
    for r in conn.execute(
        "SELECT project_type, action, cost_tokens, sample_count FROM cost_baselines WHERE project IS NULL ORDER BY project_type, action"
    ):
        pt = r["project_type"]
        if pt not in estimation_by_type:
            estimation_by_type[pt] = {}
        estimation_by_type[pt][r["action"]] = {
            "avg_cost": r["cost_tokens"],
            "samples": r["sample_count"],
        }
    if estimation_by_type:
        result["estimation_by_type"] = estimation_by_type

    accuracy: dict[str, dict[str, Any]] = {}
    for r in conn.execute("SELECT key, value FROM meta WHERE key LIKE 'tuning:%'"):
        action = r["key"][7:]
        try:
            td = json.loads(r["value"])
            if td.get("count", 0) > 1:
                accuracy[action] = {
                    "avg_cost": td.get("avg_cost", 0),
                    "stddev": td.get("cost_stddev", 0),
                    "ci_95": td.get("cost_ci_95"),
                    "samples": td.get("count", 0),
                    "success_rate": td.get("success_rate", 0),
                }
        except (json.JSONDecodeError, TypeError):
            pass
    if accuracy:
        result["estimation_accuracy"] = accuracy


async def handle_recommend(args: dict) -> dict[str, Any]:
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
            pt = ""
            gt = ""
            if project:
                row = conn.execute(
                    "SELECT project_type FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
                    (project,),
                ).fetchone()
                if row:
                    pt = row["project_type"] or ""
                gr = conn.execute("SELECT value FROM meta WHERE key = ?", (f"goal:{project}",)).fetchone()
                if gr:
                    try:
                        gv = json.loads(gr["value"])
                        if isinstance(gv, dict):
                            gt = gv.get("text", "")
                    except (json.JSONDecodeError, TypeError):
                        pass
            result = _estimate(pt, gt, conn)
            if detail and project:
                _enrich_detail(result, conn)
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
            result = _plan(project, target, conn, get_config())
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
        result = _recommend(project, conn, get_config(), cursor=cursor_val, max_cost=max_cost)

        if detail:
            _enrich_detail(result, conn)

        if fmt == "ascii":
            tree_str = _tree(project, conn, cursor=cursor_val, up_depth=up_depth)
            result["tree"] = tree_str
            result["tree_format"] = "ascii"

        return ok(result)
    finally:
        _read_lock_release()
