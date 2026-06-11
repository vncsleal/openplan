from __future__ import annotations

import math
import sqlite3
from collections import Counter, deque
from typing import Any

from openplan.core.activation import recompute_all_dirty


_state_uncertainty_cache: tuple[float, int] | None = None


def _invalidate_graph_cache() -> None:
    global _state_uncertainty_cache
    _state_uncertainty_cache = None


def _state_uncertainty(state_id: str, conn: sqlite3.Connection) -> float:
    rows = conn.execute("SELECT prob FROM edges WHERE source_id = ?", (state_id,)).fetchall()
    if not rows:
        return 1.0
    return 1.0 - max(r["prob"] for r in rows)


def _get_frontier_states(project: str, conn: sqlite3.Connection, config: dict[str, Any]) -> list[dict]:
    threshold = config.get("activation_threshold", 0.5)
    rows = conn.execute(
        "SELECT DISTINCT n.id, n.label, n.activation FROM nodes n "
        "INNER JOIN edges e ON e.source_id = n.id "
        "WHERE n.project = ? AND n.activation > ?",
        (project, threshold),
    ).fetchall()
    return [dict(r) for r in rows]


def _observe_search(project: str, query: str, conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        from openplan.core.embedding import get_cache, get_provider
        provider = get_provider()
        if provider.loaded:
            cache = get_cache()
            emb_results = cache.query(query, conn, top_k=10)
            if emb_results:
                return {"mode": "similarity", "method": "embedding", "query": query, "states": emb_results, "count": len(emb_results)}
    except Exception:
        pass
    try:
        rows = conn.execute(
            "SELECT n.* FROM nodes_fts f JOIN nodes n ON n.rowid = f.rowid "
            "WHERE f.project MATCH ? AND nodes_fts MATCH ? ORDER BY rank",
            (project, query),
        ).fetchall()
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        like = f"%{query}%"
        rows = conn.execute(
            "SELECT * FROM nodes WHERE project = ? AND (label LIKE ? OR project LIKE ?)",
            (project, like, like),
        ).fetchall()
    return {"mode": "similarity", "method": "fts5", "query": query, "states": [dict(r) for r in rows], "count": len(rows)}


def observe(project: str, query: str | None, scope: str, conn: sqlite3.Connection, config: dict[str, Any], session_id: str = "") -> dict[str, Any]:
    if query:
        return _observe_search(project, query, conn)

    if scope == "cluster":
        rows = conn.execute("SELECT * FROM nodes WHERE project = ? ORDER BY activation DESC", (project,)).fetchall()

        def _cluster_key(r: sqlite3.Row) -> str:
            label = r["label"] or r["id"]
            prefix = label.split()[0] if label else r["id"]
            act = r["activation"]
            bucket = "high" if act > 0.7 else ("mid" if act > 0.3 else "low")
            return f"{bucket}/{prefix}"

        clusters: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            clusters.setdefault(_cluster_key(r), []).append(r)
        return {"mode": "cluster", "clusters": {k: [dict(r) for r in members] for k, members in clusters.items()}, "state_count": len(rows), "cluster_count": len(clusters)}

    if scope == "all":
        rows = conn.execute("SELECT * FROM nodes WHERE project = ? ORDER BY activation DESC", (project,)).fetchall()
        return {"mode": "all", "states": [dict(r) for r in rows], "count": len(rows)}

    if scope == "rank":
        pr = _compute_pagerank(project, conn)
        rows = conn.execute("SELECT * FROM nodes WHERE project = ?", (project,)).fetchall()
        ranked = sorted(rows, key=lambda r: pr.get(r["id"], 0.0), reverse=True)
        return {"mode": "rank", "states": [dict(r) for r in ranked], "pagerank": {r["id"]: pr.get(r["id"], 0.0) for r in ranked}, "count": len(ranked)}

    recompute_all_dirty(conn, config)
    frontier = _get_frontier_states(project, conn, config)

    node_count = conn.execute("SELECT COUNT(*) AS cnt FROM nodes WHERE project = ?", (project,)).fetchone()["cnt"]
    edge_count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ?",
        (project,),
    ).fetchone()["cnt"]
    density = (edge_count / (node_count * (node_count - 1))) if node_count > 1 else 0.0

    all_node_rows = conn.execute("SELECT id FROM nodes WHERE project = ?", (project,)).fetchall()
    edge_rows = conn.execute(
        "SELECT source_id, target_id FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ?",
        (project,),
    ).fetchall()

    global _state_uncertainty_cache
    if _state_uncertainty_cache is not None and _state_uncertainty_cache[1] == edge_count:
        avg_path_length = _state_uncertainty_cache[0]
    else:
        if len(all_node_rows) > 1 and len(edge_rows) > 0:
            adj: dict[str, list[str]] = {r["id"]: [] for r in all_node_rows}
            for e in edge_rows:
                adj.setdefault(e["source_id"], []).append(e["target_id"])
                adj.setdefault(e["target_id"], [])
            total_dist = 0
            pair_count = 0
            for src in adj:
                visited: dict[str, int] = {src: 0}
                q = deque([src])
                while q:
                    node = q.popleft()
                    for nb in adj.get(node, []):
                        if nb not in visited:
                            visited[nb] = visited[node] + 1
                            q.append(nb)
                total_dist += sum(visited.values())
                pair_count += len(visited) - 1
            avg_path_length = total_dist / pair_count if pair_count > 0 else 0.0
        else:
            avg_path_length = 0.0
        _state_uncertainty_cache = (avg_path_length, edge_count)

    if node_count > 0:
        degree_count: dict[str, int] = {r["id"]: 0 for r in all_node_rows}
        for e in edge_rows:
            degree_count[e["source_id"]] = degree_count.get(e["source_id"], 0) + 1
            degree_count[e["target_id"]] = degree_count.get(e["target_id"], 0) + 1
        n = len(all_node_rows)
        deg_dist = Counter(degree_count.values())
        graph_entropy = 0.0
        for cnt in deg_dist.values():
            p = cnt / n
            if p > 0:
                graph_entropy -= p * math.log2(p)
    else:
        graph_entropy = 0.0

    recommended = max(frontier, key=lambda s: s["activation"] * (1.0 - _state_uncertainty(s["id"], conn)))["id"] if frontier else None
    health = _graph_health(project, conn) if node_count > 0 else None

    return {
        "mode": "frontier", "states": [dict(s) for s in frontier],
        "graph": {
            "density": density, "avg_path_length": avg_path_length,
            "node_count": node_count, "edge_count": edge_count, "entropy": graph_entropy,
            "health": {
                "issues": health["issues"] if health else [],
                "orphan_count": health["orphan_count"] if health else 0,
                "calibration_count": health["calibration_count"] if health else 0,
                "action_types": len(health["actions_used"]) if health else 0,
            } if health and health["issues"] else None,
        },
        "recommended": recommended,
    }


def _compute_pagerank(project: str, conn: sqlite3.Connection, iterations: int = 20, damping: float = 0.85) -> dict[str, float]:
    nodes = conn.execute("SELECT id FROM nodes WHERE project = ?", (project,)).fetchall()
    n = len(nodes)
    if n == 0:
        return {}
    node_ids = [r["id"] for r in nodes]
    out_edges: dict[str, list[tuple[str, float]]] = {}
    for nid in node_ids:
        rows = conn.execute("SELECT target_id, prob FROM edges WHERE source_id = ?", (nid,)).fetchall()
        out_edges[nid] = [(r["target_id"], r["prob"]) for r in rows]
    in_links: dict[str, list[tuple[str, float]]] = {nid: [] for nid in node_ids}
    for src in node_ids:
        total = sum(p for _, p in out_edges[src])
        if total <= 0:
            continue
        for tgt, prob in out_edges[src]:
            if tgt in in_links:
                in_links[tgt].append((src, prob / total))
    pr: dict[str, float] = {nid: 1.0 / n for nid in node_ids}
    for _ in range(iterations):
        new_pr: dict[str, float] = {}
        for nid in node_ids:
            rank_sum = sum(pr[src] * w for src, w in in_links[nid])
            new_pr[nid] = (1 - damping) / n + damping * rank_sum
        pr = new_pr
    return pr


def _graph_health(project: str, conn: sqlite3.Connection) -> dict[str, Any]:
    state_count = conn.execute("SELECT COUNT(*) AS cnt FROM nodes WHERE project = ?", (project,)).fetchone()["cnt"]
    edge_count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ?",
        (project,),
    ).fetchone()["cnt"]

    orphans = [
        dict(r) for r in conn.execute(
            "SELECT n.id, n.label, n.activation FROM nodes n "
            "WHERE n.project = ? AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.source_id = n.id) "
            "AND n.id != (SELECT MIN(n2.id) FROM nodes n2 WHERE n2.project = ?) "
            "ORDER BY n.activation DESC LIMIT 50",
            (project, project),
        ).fetchall()
    ]

    roots = conn.execute(
        "SELECT id FROM nodes WHERE project = ? AND id NOT IN "
        "(SELECT target_id FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ?)",
        (project, project),
    ).fetchall()
    all_edges = conn.execute(
        "SELECT source_id, target_id FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ?",
        (project,),
    ).fetchall()
    adj: dict[str, list[str]] = {}
    for r in all_edges:
        adj.setdefault(r["source_id"], []).append(r["target_id"])
        adj.setdefault(r["target_id"], [])

    max_depth = 0
    for root in roots:
        visited: dict[str, int] = {root["id"]: 0}
        stack = [root["id"]]
        while stack:
            node = stack.pop()
            for nb in adj.get(node, []):
                if nb not in visited:
                    visited[nb] = visited[node] + 1
                    max_depth = max(max_depth, visited[nb])
                    stack.append(nb)

    actions_used = [
        dict(r) for r in conn.execute(
            "SELECT action, COUNT(*) AS cnt FROM edges e JOIN nodes n ON n.id = e.source_id "
            "WHERE n.project = ? GROUP BY action ORDER BY cnt DESC",
            (project,),
        ).fetchall()
    ]

    calibration_count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM edges e JOIN nodes n ON n.id = e.source_id "
        "WHERE n.project = ? AND e.weight_history IS NOT NULL AND e.weight_history != '[]'",
        (project,),
    ).fetchone()["cnt"]

    orphan_count = len(orphans)
    issues: list[dict[str, Any]] = []
    if orphan_count > 0:
        issues.append({
            "code": "HIGH_ORPHAN_COUNT",
            "severity": "high" if orphan_count > 10 else "medium",
            "message": f"{orphan_count} states have no outgoing edges and are never acted upon",
            "fix": "Branch from each orphan state with actionable options, then act and learn",
        })
    if len(actions_used) == 0:
        issues.append({
            "code": "EMPTY_GRAPH", "severity": "high",
            "message": "No edges exist",
            "fix": "Use branch() with action verbs, then act() to traverse",
        })
    elif len(actions_used) <= 1:
        issues.append({
            "code": "LOW_ACTION_DIVERSITY", "severity": "medium",
            "message": f"Only 1 action type used ({actions_used[0]['action']})",
            "fix": "Use domain verbs (implement, research, review, test, deploy) in branch() options",
        })
    if calibration_count == 0 and edge_count > 0:
        issues.append({
            "code": "NO_CALIBRATION", "severity": "high",
            "message": "No edges have been calibrated",
            "fix": "After each act(), call learn(from_state, to_state, outcome, actual_cost)",
        })
    if max_depth <= 1 and state_count > 3:
        issues.append({
            "code": "SHALLOW_GRAPH", "severity": "medium",
            "message": f"Graph depth is {max_depth}",
            "fix": "Branch again from the result state after acting, to build depth",
        })

    return {
        "state_count": state_count, "edge_count": edge_count, "max_depth": max_depth,
        "root_count": len(roots), "orphan_count": orphan_count, "orphans": orphans[:10],
        "calibration_count": calibration_count, "actions_used": actions_used, "issues": issues,
    }


def diagnostics(project: str, conn: sqlite3.Connection) -> dict[str, Any]:
    h = _graph_health(project, conn)
    event_types = [
        dict(r) for r in conn.execute(
            "SELECT event_type, COUNT(*) AS cnt FROM events WHERE project = ? GROUP BY event_type",
            (project,),
        ).fetchall()
    ]
    calibration_rate = h["calibration_count"] / h["edge_count"] if h["edge_count"] > 0 else 0.0
    return {
        "project": project,
        "overview": {
            "states": h["state_count"], "edges": h["edge_count"],
            "events": conn.execute("SELECT COUNT(*) AS cnt FROM events WHERE project = ?", (project,)).fetchone()["cnt"],
            "max_depth": h["max_depth"], "root_states": h["root_count"],
            "leaf_states": h["orphan_count"],
            "avg_out_degree": round(h["edge_count"] / h["state_count"], 2) if h["state_count"] > 0 else 0.0,
        },
        "health": {
            "calibrated_edges": h["calibration_count"],
            "calibration_rate": round(calibration_rate, 4),
            "action_types": len(h["actions_used"]),
        },
        "actions_used": h["actions_used"], "event_types": event_types,
        "orphans": h["orphans"], "orphan_count": h["orphan_count"],
        "issues": h["issues"],
    }
