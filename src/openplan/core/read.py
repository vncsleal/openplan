from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from openplan.core.errors import InvalidStateError, InvalidStatusError, NoPathError
from openplan.core.graph import _get_frontier_states, _graph_health
from openplan.core.planner import plan as _plan
from openplan.core.reasoning import REASONING_FIELDS, STATUS_VALUES, ReasoningPayload
from openplan.core.state import _now, _record_event


def read_state(state_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
    node = conn.execute("SELECT * FROM nodes WHERE id = ?", (state_id,)).fetchone()
    if not node:
        raise InvalidStateError(state_id)
    node = dict(node)
    props = json.loads(node["props"]) if isinstance(node["props"], str) else node["props"]
    reasoning = ReasoningPayload.from_props(props)
    edges_out = [dict(r) for r in conn.execute(
        "SELECT e.*, n.label AS target_label FROM edges e JOIN nodes n ON n.id = e.target_id WHERE e.source_id = ?",
        (state_id,),
    ).fetchall()]
    edges_in = [dict(r) for r in conn.execute(
        "SELECT e.*, n.label AS source_label FROM edges e JOIN nodes n ON n.id = e.source_id WHERE e.target_id = ?",
        (state_id,),
    ).fetchall()]
    events = [dict(r) for r in conn.execute(
        "SELECT id, project, event_type, payload, session_id, created_at FROM events WHERE node_id = ? ORDER BY created_at DESC LIMIT 100",
        (state_id,),
    ).fetchall()]
    return {
        "ok": True,
        "state": {
            "id": node["id"],
            "label": node["label"],
            "project": node["project"],
            "status": node.get("status", "pending"),
            "activation": node["activation"],
            "frontier": bool(node["frontier"]),
            "reasoning": reasoning.to_dict(),
            "props": {k: v for k, v in props.items() if k not in REASONING_FIELDS},
            "created_at": node["created_at"],
            "updated_at": node["updated_at"],
        },
        "edges_out": edges_out,
        "edges_in": edges_in,
        "events": events,
    }


def update_state(
    state_id: str,
    conn: sqlite3.Connection,
    status: str | None = None,
    props_patch: dict[str, Any] | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    node = conn.execute("SELECT * FROM nodes WHERE id = ?", (state_id,)).fetchone()
    if not node:
        raise InvalidStateError(state_id)

    updated_fields = []

    cascade_count = 0
    if status is not None:
        if status not in STATUS_VALUES:
            raise InvalidStatusError(status)
        conn.execute("UPDATE nodes SET status = ?, updated_at = ? WHERE id = ?",
                     (status, _now(), state_id))
        updated_fields.append("status")
        if status == "blocked":
            stack = [state_id]
            while stack:
                nid = stack.pop()
                for child in conn.execute("SELECT target_id FROM edges WHERE source_id = ?", (nid,)).fetchall():
                    tid = child["target_id"]
                    st = conn.execute("SELECT status FROM nodes WHERE id = ?", (tid,)).fetchone()
                    if st and st["status"] not in ("done", "superseded", "blocked", "cascade_blocked"):
                        conn.execute("UPDATE nodes SET status = 'cascade_blocked', updated_at = ? WHERE id = ?",
                                     (_now(), tid))
                        _record_event(conn, tid, node["project"], "blocked_cascade",
                                      {"source": state_id, "blocked_by": state_id}, session_id)
                        cascade_count += 1
                        stack.append(tid)

    if props_patch:
        current_props = json.loads(node["props"]) if isinstance(node["props"], str) else node["props"]
        new_props = dict(current_props)
        for k, v in props_patch.items():
            new_props[k] = v
            updated_fields.append(k)
        conn.execute("UPDATE nodes SET props = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(new_props), _now(), state_id))

    _record_event(conn, state_id, node["project"], "updated", {
        "action": "updated",
        "state_id": state_id,
        "updated_fields": updated_fields,
        "status": status,
        "props_patch": props_patch,
    }, session_id)

    return {
        "ok": True,
        "state_id": state_id,
        "updated_fields": updated_fields,
        "cascade_count": cascade_count,
    }


def reconstruct(
    project: str,
    conn: sqlite3.Connection,
    cursor: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = config or {}

    root = conn.execute(
        "SELECT id, label FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
        (project,),
    ).fetchone()

    act_events = [dict(r) for r in conn.execute(
        "SELECT payload, created_at FROM events WHERE project = ? AND event_type = 'acted' ORDER BY created_at DESC LIMIT 10",
        (project,),
    ).fetchall()]

    all_ids: set[str] = set()
    for evt in act_events:
        try:
            p = json.loads(evt["payload"]) if isinstance(evt["payload"], str) else evt["payload"]
            if "source" in p:
                all_ids.add(p["source"])
            if "target" in p:
                all_ids.add(p["target"])
        except (json.JSONDecodeError, TypeError):
            pass

    label_map: dict[str, str] = {}
    if all_ids:
        placeholders = ",".join("?" * len(all_ids))
        for r in conn.execute(
            f"SELECT id, label FROM nodes WHERE id IN ({placeholders})",
            tuple(all_ids),
        ).fetchall():
            label_map[r["id"]] = r["label"]

    recent_path = []
    for evt in reversed(act_events):
        try:
            payload = json.loads(evt["payload"]) if isinstance(evt["payload"], str) else evt["payload"]
        except (json.JSONDecodeError, TypeError):
            continue
        if "source" in payload and "target" in payload and "action" in payload:
            recent_path.append({
                "from": payload["source"],
                "from_label": label_map.get(payload["source"], ""),
                "action": payload["action"],
                "to": payload["target"],
                "to_label": label_map.get(payload["target"], ""),
                "evidence": payload.get("evidence"),
            })

    all_nodes = [dict(r) for r in conn.execute(
        "SELECT id, label, status, activation, props, updated_at, created_at FROM nodes WHERE project = ?",
        (project,),
    ).fetchall()]

    threshold = config.get("activation_threshold", 0.5)
    frontier: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    status_counts: dict[str, int] = defaultdict(int)
    type_counts: dict[str, int] = defaultdict(int)
    stale_cutoff = 7
    stale_count = 0

    for n in all_nodes:
        st = n.get("status", "pending")
        status_counts[st] += 1
        props = json.loads(n["props"]) if isinstance(n["props"], str) else n["props"]
        type_counts[props.get("type", "unknown")] += 1
        act = n["activation"]
        if st in ("pending", "in_progress") and act > threshold:
            frontier.append({"id": n["id"], "label": n["label"], "status": st, "activation": act})
        if st == "blocked":
            blockers.append({"id": n["id"], "label": n["label"], "activation": act})
        if st == "pending":
            try:
                updated = datetime.fromisoformat(n["updated_at"].replace("Z", "+00:00"))
                if (datetime.now(timezone.utc) - updated).days >= stale_cutoff:
                    stale_count += 1
            except (ValueError, TypeError):
                pass

    edge_count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ?",
        (project,),
    ).fetchone()["cnt"]

    calibration_count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM edges e JOIN nodes n ON n.id = e.source_id "
        "WHERE n.project = ? AND e.weight_history IS NOT NULL AND e.weight_history != '[]'",
        (project,),
    ).fetchone()["cnt"]

    orphan_count = len([n for n in all_nodes if not conn.execute(
        "SELECT 1 FROM edges WHERE source_id = ? LIMIT 1", (n["id"],)
    ).fetchone() and n["id"] != (root["id"] if root else None)])

    max_depth = 0
    if root:
        raw_edges = conn.execute(
            "SELECT source_id, target_id FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ?",
            (project,),
        ).fetchall()
        adj: dict[str, list[str]] = {}
        for r in raw_edges:
            adj.setdefault(r["source_id"], []).append(r["target_id"])
            adj.setdefault(r["target_id"], [])
        visited: dict[str, int] = {root["id"]: 0}
        stack = [root["id"]]
        while stack:
            node = stack.pop()
            for nb in adj.get(node, []):
                if nb not in visited:
                    visited[nb] = visited[node] + 1
                    max_depth = max(max_depth, visited[nb])
                    stack.append(nb)

    total = len(all_nodes)
    done_count = status_counts.get("done", 0)
    pct_complete = round(done_count / total * 100, 1) if total > 0 else 0.0

    next_target = None
    if not cursor:
        cursor = root["id"] if root else None
    if cursor:
        pending = sorted(
            [n for n in all_nodes if n["id"] != cursor and n.get("status") in ("pending", "in_progress")],
            key=lambda n: -n["activation"],
        )[:5]
        if pending:
            best = pending[0]
            next_target = {"id": best["id"], "label": best["label"], "activation": round(best["activation"], 4)}

    open_insights = []
    for r in conn.execute(
        "SELECT e.weight_history FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ? ORDER BY e.updated_at DESC LIMIT 50",
        (project,),
    ).fetchall():
        try:
            wh = json.loads(r["weight_history"]) if isinstance(r["weight_history"], str) else (r["weight_history"] or [])
            for entry in wh:
                text = entry.get("insight", "")
                if text:
                    open_insights.append({"text": text, "applied": False})
        except (json.JSONDecodeError, TypeError):
            pass

    goal: dict[str, Any] | None = None
    goal_row = conn.execute("SELECT value FROM meta WHERE key = ?", (f"goal:{project}",)).fetchone()
    if goal_row:
        try:
            goal = json.loads(goal_row["value"])
        except (json.JSONDecodeError, TypeError):
            pass

    node_tree: dict[str, dict[str, Any]] = {}
    for n in all_nodes:
        node_tree[n["id"]] = {"id": n["id"], "label": n["label"], "status": n.get("status", "pending"), "children": []}
    for n in all_nodes:
        pid = n.get("parent_id")
        if pid and pid in node_tree:
            node_tree[pid]["children"].append(node_tree[n["id"]])
    tree_roots = [v for k, v in node_tree.items() if not any(
        n2.get("parent_id") == k for n2 in all_nodes
    )]

    self_tuning_info = None
    bandit_row = conn.execute(
        "SELECT value FROM meta WHERE key = 'self_tuning:bandit'"
    ).fetchone()
    if bandit_row:
        try:
            bd = json.loads(bandit_row["value"])
            chosen = bd.get("chosen_arm")
            arms = bd.get("arms", {})
            if chosen and chosen in arms:
                ab = arms[chosen]
                alpha = ab.get("alpha", 1)
                beta = ab.get("beta", 1)
                total_trials = alpha + beta - 2
                acceptance_rate = alpha / (alpha + beta)
                if total_trials < 5:
                    convergence = "low_data"
                elif acceptance_rate > 0.65:
                    convergence = "converging"
                else:
                    convergence = "exploring"
                count_row = conn.execute(
                    "SELECT value FROM meta WHERE key = 'self_tuning:act_count'"
                ).fetchone()
                act_count = json.loads(count_row["value"]) if count_row else 0
                tune_interval = (config or {}).get("tune_interval", 10)
                self_tuning_info = {
                    "bandit_arm": chosen,
                    "acceptance_rate": round(acceptance_rate, 3),
                    "convergence": convergence,
                    "total_trials": total_trials,
                    "acts_since_tune": act_count,
                    "tune_due": act_count >= tune_interval,
                }
        except (json.JSONDecodeError, TypeError, ZeroDivisionError):
            pass

    return {
        "ok": True,
        "project": project,
        "cursor": cursor,
        "root": {"id": root["id"], "label": root["label"]} if root else None,
        "goal": goal,
        "tree": tree_roots,
        "recent_path": recent_path,
        "frontier": frontier,
        "blockers": blockers,
        "open_insights": open_insights[:10],
        "next_target": next_target,
        "self_tuning": self_tuning_info,
        "project_health": {
            "pct_complete": pct_complete,
            "total_states": total,
            "completed": done_count,
            "calibrated_count": calibration_count,
            "stale_count": stale_count,
            "orphan_count": orphan_count,
            "edge_count": edge_count,
            "max_depth": max_depth,
            "calibration_rate": round(
                calibration_count / edge_count, 4
            ) if edge_count > 0 else 0.0,
        },
    }


def compare_states(
    state_a: str,
    state_b: str,
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    raw_a = conn.execute("SELECT * FROM nodes WHERE id = ?", (state_a,)).fetchone()
    raw_b = conn.execute("SELECT * FROM nodes WHERE id = ?", (state_b,)).fetchone()
    if not raw_a:
        raise InvalidStateError(state_a)
    if not raw_b:
        raise InvalidStateError(state_b)
    node_a = dict(raw_a)
    node_b = dict(raw_b)

    def _extract_props(n: dict[str, Any]) -> dict[str, Any]:
        raw = n.get("props", "{}")
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return {}

    props_a = _extract_props(node_a)
    props_b = _extract_props(node_b)
    all_keys = set(props_a.keys()) | set(props_b.keys())
    only_in_a = {k: props_a[k] for k in all_keys if k in props_a and k not in props_b}
    only_in_b = {k: props_b[k] for k in all_keys if k in props_b and k not in props_a}
    changed = {k: {"a": props_a[k], "b": props_b[k]} for k in all_keys if k in props_a and k in props_b and props_a[k] != props_b[k]}
    identical = {k: props_a[k] for k in all_keys if k in props_a and k in props_b and props_a[k] == props_b[k] and not isinstance(props_a[k], dict)}

    edges_out_a = [dict(r) for r in conn.execute(
        "SELECT target_id, action, cost_tokens, prob FROM edges WHERE source_id = ?", (state_a,)
    ).fetchall()]
    edges_out_b = [dict(r) for r in conn.execute(
        "SELECT target_id, action, cost_tokens, prob FROM edges WHERE source_id = ?", (state_b,)
    ).fetchall()]
    edges_in_a = [dict(r) for r in conn.execute(
        "SELECT source_id, action FROM edges WHERE target_id = ?", (state_a,)
    ).fetchall()]
    edges_in_b = [dict(r) for r in conn.execute(
        "SELECT source_id, action FROM edges WHERE target_id = ?", (state_b,)
    ).fetchall()]

    out_targets_a = {e["target_id"] for e in edges_out_a}
    out_targets_b = {e["target_id"] for e in edges_out_b}
    in_sources_a = {e["source_id"] for e in edges_in_a}
    in_sources_b = {e["source_id"] for e in edges_in_b}

    event_count_a = conn.execute("SELECT COUNT(*) AS cnt FROM events WHERE node_id = ?", (state_a,)).fetchone()["cnt"]
    event_count_b = conn.execute("SELECT COUNT(*) AS cnt FROM events WHERE node_id = ?", (state_b,)).fetchone()["cnt"]
    recent_a = [dict(r) for r in conn.execute(
        "SELECT event_type, created_at FROM events WHERE node_id = ? ORDER BY created_at DESC LIMIT 3", (state_a,)
    ).fetchall()]
    recent_b = [dict(r) for r in conn.execute(
        "SELECT event_type, created_at FROM events WHERE node_id = ? ORDER BY created_at DESC LIMIT 3", (state_b,)
    ).fetchall()]

    embedding_similarity = None
    try:
        from openplan.core.embedding import get_cache, get_provider
        if get_provider().loaded:
            cache = get_cache()
            emb_a = cache.get_embedding(state_a)
            emb_b = cache.get_embedding(state_b)
            if emb_a is not None and emb_b is not None:
                import numpy as np
                denom = np.linalg.norm(emb_a) * np.linalg.norm(emb_b)
                if denom != 0:
                    embedding_similarity = float(np.dot(emb_a, emb_b) / denom)
    except Exception:
        pass

    return {
        "ok": True,
        "state_a": {
            "id": state_a,
            "label": node_a["label"],
            "project": node_a["project"],
            "status": node_a.get("status", "pending"),
            "activation": node_a["activation"],
            "frontier": bool(node_a["frontier"]),
            "event_count": event_count_a,
            "recent_events": recent_a,
        },
        "state_b": {
            "id": state_b,
            "label": node_b["label"],
            "project": node_b["project"],
            "status": node_b.get("status", "pending"),
            "activation": node_b["activation"],
            "frontier": bool(node_b["frontier"]),
            "event_count": event_count_b,
            "recent_events": recent_b,
        },
        "props_diff": {
            "only_in_a": only_in_a,
            "only_in_b": only_in_b,
            "changed": changed,
            "identical": identical,
        },
        "status_diff": {
            "a": node_a.get("status", "pending"),
            "b": node_b.get("status", "pending"),
            "same": node_a.get("status") == node_b.get("status"),
        },
        "activation_diff": {
            "a": node_a["activation"],
            "b": node_b["activation"],
            "delta": round(node_a["activation"] - node_b["activation"], 6),
        },
        "edges_diff": {
            "shared_out_targets": list(out_targets_a & out_targets_b),
            "only_a_out_targets": list(out_targets_a - out_targets_b),
            "only_b_out_targets": list(out_targets_b - out_targets_a),
            "shared_in_sources": list(in_sources_a & in_sources_b),
            "only_a_in_sources": list(in_sources_a - in_sources_b),
            "only_b_in_sources": list(in_sources_b - in_sources_a),
        },
        "embedding_similarity": embedding_similarity,
    }


def compare_paths(
    project: str,
    conn: sqlite3.Connection,
    targets: list[str],
    config: dict[str, Any] | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    config = config or {}
    if not cursor:
        root = conn.execute(
            "SELECT id FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
            (project,),
        ).fetchone()
        cursor = root["id"] if root else None
    if not cursor:
        return {"ok": True, "results": [], "count": 0}

    results = []
    for target in targets:
        try:
            plan_result = _plan(cursor, target, conn, config)
            expected = plan_result.get("expected_cost", {})
            results.append({
                "target": target,
                "target_label": plan_result.get("resolved_target", {}).get("label", ""),
                "cost_tokens": expected.get("tokens", 0),
                "risk": expected.get("risk", 0.0),
                "steps": expected.get("steps", 0),
                "path": plan_result.get("path", []),
                "traversal": plan_result.get("traversal", []),
                "high_uncertainty": plan_result.get("high_uncertainty", False),
            })
        except NoPathError:
            results.append({"target": target, "error": "No path found"})
        except Exception as exc:
            results.append({"target": target, "error": str(exc)})

    results.sort(key=lambda r: r.get("cost_tokens", float("inf")))
    return {"ok": True, "results": results, "count": len(results), "from": cursor}


def optimize(
    project: str,
    conn: sqlite3.Connection,
    config: dict[str, Any] | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    config = config or {}
    if not cursor:
        root = conn.execute(
            "SELECT id FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
            (project,),
        ).fetchone()
        cursor = root["id"] if root else None
    if not cursor:
        return {"ok": True, "optimal_order": [], "total_cost": 0, "count": 0}

    remaining = [dict(r) for r in conn.execute(
        "SELECT id, label FROM nodes WHERE project = ? AND status NOT IN ('done', 'blocked', 'superseded') AND id != ?",
        (project, cursor),
    ).fetchall()]

    if not remaining:
        return {"ok": True, "optimal_order": [], "total_cost": 0, "count": 0}

    unvisited = set(r["id"] for r in remaining)
    label_map = {r["id"]: r["label"] for r in remaining}
    current = cursor
    order: list[dict[str, Any]] = []
    total_cost = 0.0

    while unvisited:
        best_next = None
        best_cost = float("inf")
        best_path = None
        for sid in unvisited:
            try:
                plan_result = _plan(current, sid, conn, config)
                cost = plan_result.get("expected_cost", {}).get("tokens", float("inf"))
                if cost < best_cost:
                    best_cost = cost
                    best_next = sid
                    best_path = plan_result.get("path", [])
            except NoPathError:
                continue
            except Exception:
                continue
        if best_next is None:
            break
        order.append({
            "id": best_next,
            "label": label_map.get(best_next, ""),
            "cost_from_previous": best_cost,
            "cumulative_cost": total_cost + best_cost,
            "path": best_path,
        })
        total_cost += best_cost
        unvisited.discard(best_next)
        current = best_next

    return {
        "ok": True,
        "optimal_order": order,
        "total_cost": total_cost,
        "count": len(order),
        "remaining_unreachable": len(unvisited),
        "from": cursor,
    }
