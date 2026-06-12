from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import string
from collections import Counter, deque
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger("openplan.graph")

from openplan.core.activation import recompute_all_dirty


_state_uncertainty_cache: tuple[float, int] | None = None


def _invalidate_graph_cache() -> None:
    global _state_uncertainty_cache
    _state_uncertainty_cache = None


def _score_state(nid: str, activation: float, props: dict, orphan: bool, max_visits: int, threshold: float, config: dict) -> float:
    visit_count = props.get("visit_count", 0)
    visit_ratio = visit_count / max_visits if max_visits > 0 else 0.0
    rw = config.get("recommend_weights", {"orphan": 0.35, "visit": 0.30, "activation": 0.20, "stale": 0.15})
    return (rw["orphan"] if orphan else 0.0) + rw["visit"] * (1.0 - visit_ratio) + rw["activation"] * activation + (rw["stale"] if activation < threshold else 0.0)


def _state_uncertainty(state_id: str, conn: sqlite3.Connection) -> float:
    rows = conn.execute("SELECT prob FROM edges WHERE source_id = ?", (state_id,)).fetchall()
    if not rows:
        return 1.0
    return 1.0 - max(r["prob"] for r in rows)


def _get_frontier_states(project: str, conn: sqlite3.Connection, config: dict[str, Any]) -> list[dict]:
    threshold = config.get("activation_threshold", 0.5)
    rows = conn.execute(
        "SELECT DISTINCT n.id, n.label, n.activation, n.props FROM nodes n "
        "INNER JOIN edges e ON e.source_id = n.id "
        "WHERE n.project = ? AND n.activation > ?",
        (project, threshold),
    ).fetchall()
    return [dict(r) for r in rows]


def _observe_search(project: str | None, query: str, conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        from openplan.core.embedding import get_cache, get_provider
        provider = get_provider()
        if provider.loaded:
            cache = get_cache()
            emb_results = cache.query(query, conn, top_k=10)
            if emb_results:
                return {"mode": "similarity", "method": "embedding", "query": query, "states": emb_results, "count": len(emb_results)}
    except Exception:
        _log.exception("Embedding search failed, falling back to FTS/LIKE")
    states: list[dict[str, Any]] = []
    if project:
        try:
            rows = conn.execute(
                "SELECT n.* FROM nodes_fts f JOIN nodes n ON n.rowid = f.rowid "
                "WHERE f.project MATCH ? AND nodes_fts MATCH ? ORDER BY rank",
                (project, query),
            ).fetchall()
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            _log.warning("FTS5 search failed, falling back to LIKE")
            like = f"%{query}%"
            rows = conn.execute(
                "SELECT * FROM nodes WHERE project = ? AND (label LIKE ? OR project LIKE ?)",
                (project, like, like),
            ).fetchall()
        states = [dict(r) for r in rows]
    insights = []
    if project:
        edge_rows = conn.execute(
            "SELECT e.source_id, e.target_id, e.weight_history FROM edges e "
            "JOIN nodes n ON n.id = e.source_id WHERE n.project = ?",
            (project,),
        ).fetchall()
    else:
        edge_rows = conn.execute(
            "SELECT e.source_id, e.target_id, e.weight_history FROM edges e "
            "JOIN nodes n ON n.id = e.source_id"
        ).fetchall()
    for r in edge_rows:
        try:
            wh = json.loads(r["weight_history"]) if isinstance(r["weight_history"], str) else (r["weight_history"] or [])
            for entry in wh:
                text = entry.get("insight", "")
                if query.lower() in text.lower():
                    insights.append({"source": "insight", "text": text, "from_state": r["source_id"], "to_state": r["target_id"]})
        except (json.JSONDecodeError, TypeError):
            pass

    result: dict[str, Any] = {"mode": "search" if insights else "similarity", "query": query, "states": states, "count": len(states)}
    if insights:
        result["insights"] = insights
    return result


def _suggested_next_action(last_event_type: str | None, frontier: list[dict] | None = None, recommended: str | None = None, conn: sqlite3.Connection | None = None) -> dict:
    if last_event_type is None:
        return {"tool": "plan", "reason": "project is empty — start by planning the first goal"}
    hints = {
        "acted": {"tool": "learn", "reason": "last action was executed, calibrate the edge with actual cost"},
        "calibrated": {"tool": "observe", "reason": "edge was calibrated, refresh the frontier"},
        "branched": {"tool": "act", "reason": "new options were created, traverse the frontier"},
        "init": {"tool": "branch", "reason": "project was created, explore possible approaches"},
        "compressed": {"tool": "observe", "reason": "events were archived, reassess the graph"},
    }
    result = dict(hints.get(last_event_type, {"tool": "plan", "reason": "assess the current state"}))
    if frontier and recommended:
        result["target"] = recommended
        if conn:
            edge = conn.execute(
                "SELECT action FROM edges WHERE source_id = ? LIMIT 1", (recommended,)
            ).fetchone()
            if edge:
                result["action"] = edge["action"]
    return result


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

    recommended = None
    if frontier:
        orphan_ids = {r["id"] for r in conn.execute(
            "SELECT id FROM nodes WHERE project = ? AND NOT EXISTS "
            "(SELECT 1 FROM edges WHERE source_id = nodes.id) AND id != "
            "(SELECT MIN(n2.id) FROM nodes n2 WHERE n2.project = ?)",
            (project, project),
        ).fetchall()}
        max_visits = conn.execute(
            "SELECT MAX(json_extract(props, '$.visit_count')) AS mv FROM nodes WHERE project = ?",
            (project,),
        ).fetchone()["mv"] or 0
        threshold = config.get("activation_threshold", 0.5)

        def _best(r: sqlite3.Row) -> float:
            try:
                p = json.loads(r["props"])
            except (json.JSONDecodeError, TypeError):
                p = {}
            return _score_state(r["id"], r["activation"], p, r["id"] in orphan_ids, max_visits, threshold, config)

        best = max(frontier, key=_best) if frontier else None
        recommended = best["id"] if best else None

    health = _graph_health(project, conn) if node_count > 0 else None

    last_event = conn.execute(
        "SELECT event_type FROM events WHERE project = ? ORDER BY created_at DESC LIMIT 1",
        (project,),
    ).fetchone()
    suggested = _suggested_next_action(last_event["event_type"] if last_event else None, frontier, recommended, conn)

    return {
        "mode": "frontier", "states": [dict(s) for s in frontier],
        "graph": {
            "density": density, "avg_path_length": avg_path_length,
            "node_count": node_count, "edge_count": edge_count, "entropy": graph_entropy,
            "health": health if health else None,
        },
        "recommended": recommended,
        "suggested_next_action": suggested,
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
            "fix": {"tool": "branch", "action": "implement"},
            "notify": state_count > 5,
        })
    if len(actions_used) == 0:
        issues.append({
            "code": "EMPTY_GRAPH", "severity": "high",
            "message": "No edges exist",
            "fix": {"tool": "branch", "action": "implement"},
            "notify": True,
        })
    elif len(actions_used) <= 1:
        issues.append({
            "code": "LOW_ACTION_DIVERSITY", "severity": "medium",
            "message": f"Only 1 action type used ({actions_used[0]['action']})",
            "fix": {"tool": "branch", "action": "research"},
            "notify": state_count > 5,
        })
    if calibration_count == 0 and edge_count > 0:
        issues.append({
            "code": "NO_CALIBRATION", "severity": "high",
            "message": "No edges have been calibrated",
            "fix": {"tool": "learn", "action": "implement"},
            "notify": state_count > 5,
        })
    if max_depth <= 1 and state_count > 3:
        issues.append({
            "code": "SHALLOW_GRAPH", "severity": "medium",
            "message": f"Graph depth is {max_depth}",
            "fix": {"tool": "branch", "action": "implement"},
            "notify": state_count > 5,
        })

    return {
        "state_count": state_count, "edge_count": edge_count, "max_depth": max_depth,
        "root_count": len(roots), "orphan_count": orphan_count, "orphans": orphans[:10],
        "calibration_count": calibration_count, "actions_used": actions_used, "issues": issues,
    }


def diagnostics(project: str, conn: sqlite3.Connection, config: dict[str, Any] | None = None, auto_fix: bool = False) -> dict[str, Any]:
    h = _graph_health(project, conn)
    event_types = [
        dict(r) for r in conn.execute(
            "SELECT event_type, COUNT(*) AS cnt FROM events WHERE project = ? GROUP BY event_type",
            (project,),
        ).fetchall()
    ]
    calibration_rate = h["calibration_count"] / h["edge_count"] if h["edge_count"] > 0 else 0.0
    fixes_applied = 0

    root = conn.execute(
        "SELECT id FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
        (project,),
    ).fetchone()
    if auto_fix and root:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        for issue in h["issues"]:
            if issue["code"] == "HIGH_ORPHAN_COUNT" and fixes_applied < 10:
                for orphan in h["orphans"][:5]:
                    if orphan["id"] == root["id"]:
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO edges (source_id, target_id, action, prob, created_at, updated_at) VALUES (?, ?, 'implement', 0.5, ?, ?)",
                        (orphan["id"], root["id"], now, now),
                    )
                    fixes_applied += 1
        if fixes_applied and root:
            eid = conn.execute("SELECT COALESCE(MAX(CAST(SUBSTR(id, 3) AS INTEGER)), 0) + 1 FROM events").fetchone()[0]
            ikey = hashlib.sha256(f"{root['id']}:auto_fix".encode()).hexdigest()[:32]
            conn.execute(
                "INSERT OR IGNORE INTO events (id, project, node_id, event_type, payload, version, idempotency_key, session_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, '', ?)",
                (f"E-{eid:06d}", project, root["id"], "auto_fix",
                 json.dumps({"fixes_applied": fixes_applied, "issue_codes": [i["code"] for i in h["issues"]]}),
                 ikey, now),
            )

    result: dict[str, Any] = {
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
    if fixes_applied:
        result["fixes_applied"] = fixes_applied
    return result


def _tokenize(text: str) -> list[str]:
    tokens = text.lower().split()
    cleaned = []
    for token in tokens:
        t = token.strip(string.punctuation)
        if t:
            cleaned.append(t)
    return cleaned


def _token_score(query_tokens: list[str], text_token_set: set[str]) -> float:
    if not query_tokens:
        return 0.0
    matches = sum(1 for qt in query_tokens if qt in text_token_set)
    return matches / len(query_tokens)


def search(query: str, conn: sqlite3.Connection, project: str | None = None, limit: int = 20) -> dict[str, Any]:
    if project:
        projects = [dict(r) for r in conn.execute(
            "SELECT n.project, MIN(n.id) AS root_id, n.label, COUNT(DISTINCT e.id) AS events "
            "FROM nodes n LEFT JOIN events e ON e.project = n.project "
            "WHERE n.project = ? "
            "GROUP BY n.project",
            (project,),
        ).fetchall()]
    else:
        projects = [dict(r) for r in conn.execute(
            "SELECT n.project, n.id AS root_id, n.label, COUNT(e.id) AS events "
            "FROM nodes n LEFT JOIN events e ON e.project = n.project "
            "WHERE n.id IN (SELECT MIN(n2.id) FROM nodes n2 GROUP BY n2.project) "
            "GROUP BY n.project ORDER BY events DESC"
        ).fetchall()]

    query_tokens = _tokenize(query)

    if project:
        all_nodes = [dict(r) for r in conn.execute(
            "SELECT id, label, project, activation, status FROM nodes WHERE project = ?",
            (project,),
        ).fetchall()]
    else:
        all_nodes = [dict(r) for r in conn.execute(
            "SELECT id, label, project, activation, status FROM nodes"
        ).fetchall()]

    scored = []
    for node in all_nodes:
        label_tokens = _tokenize(node["label"])
        score = _token_score(query_tokens, set(label_tokens))
        if score > 0:
            matched = [t for t in query_tokens if t in set(label_tokens)]
            scored.append((score, node["activation"], node, matched))

    scored.sort(key=lambda x: (-x[0], -x[1]))
    matched_states = []
    for score, activation, node, matched in scored[:limit]:
        entry = {"id": node["id"], "label": node["label"], "project": node["project"], "activation": activation, "status": node.get("status", "pending")}
        if matched:
            entry["matched_tokens"] = matched
        matched_states.append(entry)

    if not matched_states:
        like_q = f"%{query}%"
        if project:
            rows = conn.execute(
                "SELECT id, label, project, activation, status FROM nodes WHERE project = ? AND label LIKE ? ORDER BY activation DESC LIMIT ?",
                (project, like_q, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, label, project, activation, status FROM nodes WHERE label LIKE ? ORDER BY activation DESC LIMIT ?",
                (like_q, limit),
            ).fetchall()
        matched_states = [dict(r) for r in rows]

    edge_query = "SELECT e.source_id, e.target_id, e.weight_history, n.project FROM edges e JOIN nodes n ON n.id = e.source_id"
    edge_params: tuple = ()
    if project:
        edge_query += " WHERE n.project = ?"
        edge_params = (project,)
    edge_query += " ORDER BY e.updated_at DESC LIMIT 1000"

    def _match_insights(rows, match_fn) -> list[dict]:
        results = []
        for r in rows:
            try:
                wh = json.loads(r["weight_history"]) if isinstance(r["weight_history"], str) else (r["weight_history"] or [])
                for entry in wh:
                    text = entry.get("insight", "")
                    if text and match_fn(text):
                        results.append({"source": "insight", "text": text, "from_state": r["source_id"], "to_state": r["target_id"], "project": r["project"]})
            except (json.JSONDecodeError, TypeError):
                pass
        return results

    insights = _match_insights(conn.execute(edge_query, edge_params).fetchall(), lambda t: _token_score(query_tokens, set(_tokenize(t))) > 0)

    if not insights:
        insights = _match_insights(conn.execute(edge_query, edge_params).fetchall(), lambda t: query.lower() in t.lower())

    result: dict[str, Any] = {"query": query, "projects": projects, "count": len(projects), "states": matched_states}
    if insights:
        result["insights"] = insights
    return result
