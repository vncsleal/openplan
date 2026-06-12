from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from openplan.core.errors import InvalidStateError


def build_tree(
    state_id: str | None,
    project: str | None,
    conn: sqlite3.Connection,
    depth: int = 3,
    up_depth: int = 0,
    include_activation: bool = True,
    include_status: bool = True,
    include_edges: bool = False,
    fmt: str = "json",
) -> dict[str, Any]:
    if state_id:
        root = conn.execute("SELECT * FROM nodes WHERE id = ?", (state_id,)).fetchone()
        if not root:
            raise InvalidStateError(state_id)
        actual_project = root["project"]
    elif project:
        root = conn.execute(
            "SELECT * FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
            (project,),
        ).fetchone()
        if not root:
            return {"ok": True, "format": fmt, "tree": None, "node_count": 0, "max_depth": 0, "branching_factor": 0.0}
        actual_project = project
        state_id = root["id"]
    else:
        return {"ok": True, "format": fmt, "tree": None, "node_count": 0, "max_depth": 0, "branching_factor": 0.0}

    all_nodes = {r["id"]: dict(r) for r in conn.execute(
        "SELECT * FROM nodes WHERE project = ?", (actual_project,)
    ).fetchall()}

    adj: dict[str, list[dict[str, Any]]] = defaultdict(list)
    adj_rev: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in conn.execute(
        "SELECT * FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ?",
        (actual_project,),
    ).fetchall():
        e_data = dict(e)
        adj[e_data["source_id"]].append(e_data)
        adj_rev[e_data["target_id"]].append(e_data)

    visited: set[str] = set()
    assert state_id is not None

    if up_depth > 0:
        current = state_id
        for _ in range(up_depth):
            parents = adj_rev.get(current, [])
            if not parents:
                break
            current = parents[0]["source_id"]
        state_id = current

    tree_node = _build_subtree(
        state_id, all_nodes, adj, visited, depth, 0,
        include_activation, include_status, include_edges,
    )

    stats = _compute_stats(all_nodes, adj, state_id)
    tree_node["stats"] = stats

    if fmt == "ascii":
        lines: list[str] = []
        _render_ascii(tree_node, "", True, lines)
        return {
            "ok": True,
            "format": "ascii",
            "tree": "\n".join(lines),
            "node_count": stats["node_count"],
            "max_depth": stats["max_depth"],
            "branching_factor": stats["branching_factor"],
        }

    return {
        "ok": True,
        "format": "json",
        "tree": tree_node,
        "node_count": stats["node_count"],
        "max_depth": stats["max_depth"],
        "branching_factor": stats["branching_factor"],
    }


def _build_subtree(
    sid: str,
    all_nodes: dict[str, dict[str, Any]],
    adj: dict[str, list[dict[str, Any]]],
    visited: set[str],
    max_depth: int,
    current_depth: int,
    include_activation: bool,
    include_status: bool,
    include_edges: bool,
) -> dict[str, Any]:
    node = all_nodes.get(sid)
    if not node:
        return {"id": sid, "label": "???"}

    result: dict[str, Any] = {
        "id": sid,
        "label": node.get("label", ""),
    }
    if include_activation:
        result["activation"] = node.get("activation", 0.0)
    if include_status:
        result["status"] = node.get("status", "pending")

    if sid in visited:
        result["cycle"] = True
        return result
    visited.add(sid)

    if current_depth >= max_depth:
        result["truncated"] = True
        visited.discard(sid)
        return result

    children = []
    for e in adj.get(sid, []):
        child = _build_subtree(
            e["target_id"], all_nodes, adj, visited, max_depth, current_depth + 1,
            include_activation, include_status, include_edges,
        )
        if include_edges:
            child["edge"] = {
                "action": e["action"],
                "cost_tokens": e["cost_tokens"],
                "prob": e["prob"],
            }
        children.append(child)

    result["children"] = children
    visited.discard(sid)
    return result


def _compute_stats(
    all_nodes: dict[str, dict[str, Any]],
    adj: dict[str, list[dict[str, Any]]],
    root_id: str,
) -> dict[str, Any]:
    node_count = len(all_nodes)
    if not adj:
        return {"node_count": node_count, "max_depth": 0, "branching_factor": 0.0}

    stack = [(root_id, 0)]
    max_depth = 0
    while stack:
        nid, d = stack.pop()
        max_depth = max(max_depth, d)
        for e in adj.get(nid, []):
            stack.append((e["target_id"], d + 1))

    non_leaf = sum(1 for nid in adj if adj[nid])
    total_out = sum(len(edges) for edges in adj.values())
    branching = total_out / non_leaf if non_leaf > 0 else 0.0

    return {
        "node_count": node_count,
        "max_depth": max_depth,
        "branching_factor": round(branching, 2),
    }


def _render_ascii(node: dict[str, Any], prefix: str, is_last: bool, lines: list[str]) -> None:
    label = node.get("label", "???")
    sid = node.get("id", "???")
    act = node.get("activation")
    status = node.get("status")
    parts = [f"{sid} {label}"]
    if act is not None:
        parts.append(f"act={act:.3f}")
    if status:
        parts.append(status)
    line = " └── " if is_last else " ├── "
    lines.append(prefix + line + " ".join(parts))

    children = node.get("children", [])
    if not children:
        return
    child_prefix = prefix + ("     " if is_last else " │   ")
    for i, child in enumerate(children):
        _render_ascii(child, child_prefix, i == len(children) - 1, lines)
