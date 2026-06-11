from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import Counter, deque
from datetime import datetime, timezone
from typing import Any

import numpy as np

from openplan.core.activation import get_activation, increment_max_in_degree, mark_dirty

_path_length_cache: tuple[float, int] | None = None


def _invalidate_path_cache() -> None:
    global _path_length_cache
    _path_length_cache = None


def generate_id(project: str, conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT id FROM nodes ORDER BY id DESC LIMIT 1"
    ).fetchone()
    next_num = (int(row["id"][2:]) if row else 0) + 1
    return f"S-{next_num:06d}"


def generate_branch_id(project: str, conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(id, 3) AS INTEGER)) AS max_id FROM events WHERE event_type = 'branched'"
    ).fetchone()
    next_num = (row["max_id"] or 0) + 1
    return f"B-{next_num:06d}"


def generate_event_id(project: str, conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(id, 3) AS INTEGER)) AS max_id FROM events"
    ).fetchone()
    next_num = (row["max_id"] or 0) + 1
    return f"E-{next_num:06d}"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _idempotency_key(node_id: str, event_type: str, action: str = "") -> str:
    raw = f"{node_id}:{event_type}:{action}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _record_event(conn: sqlite3.Connection, node_id: str, project: str, event_type: str, payload: dict, session_id: str = "") -> str:
    eid = generate_event_id(project, conn)
    ikey = _idempotency_key(node_id, event_type, payload.get("action", ""))
    now = _now()
    conn.execute(
        "INSERT OR IGNORE INTO events (id, project, node_id, event_type, payload, version, idempotency_key, session_id, created_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
        (eid, project, node_id, event_type, json.dumps(payload), ikey, session_id, now),
    )
    return eid


def _ensure_node(project: str, label: str, conn: sqlite3.Connection) -> str:
    sid = generate_id(project, conn)
    now = _now()
    conn.execute(
        "INSERT INTO nodes (id, label, project, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (sid, label, project, now, now),
    )
    return sid


def init_project(project: str, label: str | None, conn: sqlite3.Connection, session_id: str = "") -> dict[str, Any]:
    owned_init = _safe_savepoint(conn, "init_tx")
    try:
        existing = conn.execute(
            "SELECT id, label FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
            (project,),
        ).fetchone()
        if existing:
            _safe_release(conn, "init_tx", owned_init)
            return {
                "ok": True,
                "state_id": existing["id"],
                "label": existing["label"],
                "created": False,
            }
        sid = _ensure_node(project, label or project, conn)
        now = _now()
        conn.execute(
            "UPDATE nodes SET updated_at = ? WHERE id = ?",
            (now, sid),
        )
        _record_event(conn, sid, project, "init", {"label": label or project}, session_id)
        _safe_release(conn, "init_tx", owned_init)
        return {
            "ok": True,
            "state_id": sid,
            "label": label or project,
            "created": True,
        }
    except Exception:
        _safe_rollback(conn, "init_tx", owned_init)
        raise


def _detect_cycle(conn: sqlite3.Connection, source_id: str, target_id: str, action: str) -> bool:
    """Returns True if traversing source_id -> target_id via action would create
    a cycle. Checks if a path exists from target_id back to source_id,
    excluding the direct edge being traversed."""
    row = conn.execute(
        """WITH RECURSIVE r(id) AS (
            SELECT ?
            UNION
            SELECT e.target_id FROM r
            JOIN edges e ON e.source_id = r.id
            WHERE NOT (e.source_id = ? AND e.target_id = ? AND e.action = ?)
        )
        SELECT 1 FROM r WHERE id = ? LIMIT 1""",
        (target_id, source_id, target_id, action, source_id),
    ).fetchone()
    return row is not None


def _safe_savepoint(conn: sqlite3.Connection, name: str) -> bool:
    """Create a savepoint if not inside one already. Returns True if created."""
    try:
        conn.execute(f"SAVEPOINT {name}")
        return True
    except sqlite3.OperationalError:
        return False


def _safe_release(conn: sqlite3.Connection, name: str, owned: bool) -> None:
    if owned:
        conn.execute(f"RELEASE SAVEPOINT {name}")


def _safe_rollback(conn: sqlite3.Connection, name: str, owned: bool) -> None:
    if owned:
        try:
            conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
        except sqlite3.OperationalError:
            pass


def act(
    state_id: str,
    action: str,
    conn: sqlite3.Connection,
    config: dict[str, Any],
    target: str | None = None,
    evidence: str | None = None,
    thought: str | None = None,
    expected_cost: dict | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    owned = _safe_savepoint(conn, "act_tx")
    try:
        src = conn.execute("SELECT * FROM nodes WHERE id = ?", (state_id,)).fetchone()
        if not src:
            _safe_rollback(conn, "act_tx", owned)
            return {"ok": False, "error": {"code": "INVALID_STATE", "message": f"State {state_id} not found"}}

        matching = conn.execute(
            "SELECT * FROM edges WHERE source_id = ? AND action = ?",
            (state_id, action),
        ).fetchall()

        if not matching:
            _safe_rollback(conn, "act_tx", owned)
            return {
                "ok": False,
                "error": {
                    "code": "INVALID_ACTION",
                    "message": f"No edge from {state_id} with action '{action}'",
                },
            }

        if len(matching) > 1 and target:
            matching = [e for e in matching if e["target_id"] == target]
            if not matching:
                _safe_rollback(conn, "act_tx", owned)
                return {
                    "ok": False,
                    "error": {
                        "code": "TARGET_NOT_FOUND",
                        "message": f"No edge from {state_id} with action '{action}' targeting '{target}'",
                    },
                }
        elif len(matching) > 1 and not target:
            matching = sorted(matching, key=lambda e: (-e["prob"], e["cost_tokens"]))

        edge = matching[0]
        edge = dict(edge)
        target_id = edge["target_id"]

        if _detect_cycle(conn, state_id, target_id, action):
            _safe_rollback(conn, "act_tx", owned)
            return {
                "ok": False,
                "error": {
                    "code": "CYCLE_DETECTED",
                    "message": f"Acting {state_id} -> {target_id} would create a cycle",
                },
            }

        payload = {
            "action": action,
            "source": state_id,
            "target": target_id,
            "evidence": evidence,
            "thought": thought,
            "expected_cost": expected_cost,
            "cost_actual": {
                "tokens": edge.get("cost_tokens", 10000),
                "risk": edge.get("cost_risk", 0.1),
            },
        }
        _record_event(conn, state_id, src["project"], "acted", payload, session_id)

        mark_dirty(state_id, conn)
        mark_dirty(target_id, conn)
        _invalidate_path_cache()
        get_activation(state_id, conn, config)
        get_activation(target_id, conn, config)

        cost_actual = {
            "tokens": edge.get("cost_tokens", 10000),
            "risk": edge.get("cost_risk", 0.1),
        }
        if expected_cost is not None:
            cost_delta = {
                "tokens": cost_actual["tokens"] - expected_cost.get("tokens", 0),
                "risk": cost_actual["risk"] - expected_cost.get("risk", 0.0),
            }
        else:
            cost_delta = None

        frontier = _get_frontier_states(src["project"], conn, config)
        _safe_release(conn, "act_tx", owned)
    except Exception:
        _safe_rollback(conn, "act_tx", owned)
        raise

    return {
        "ok": True,
        "next_state": target_id,
        "cursor": target_id,
        "activation_delta": {
            state_id: get_activation(state_id, conn, config),
            target_id: get_activation(target_id, conn, config),
        },
        "cost_actual": cost_actual,
        "cost_delta": cost_delta,
        "new_frontier": [s["id"] for s in frontier],
    }


def _state_uncertainty(state_id: str, conn: sqlite3.Connection) -> float:
    rows = conn.execute(
        "SELECT prob FROM edges WHERE source_id = ?", (state_id,)
    ).fetchall()
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


def _observe_search(
    project: str,
    query: str,
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    try:
        from openplan.core.embedding import get_cache, get_provider

        provider = get_provider()
        if provider.loaded:
            cache = get_cache()
            emb_results = cache.query(query, conn, top_k=10)
            if emb_results:
                return {
                    "mode": "similarity",
                    "method": "embedding",
                    "query": query,
                    "states": emb_results,
                    "count": len(emb_results),
                }
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
    return {
        "mode": "similarity",
        "method": "fts5",
        "query": query,
        "states": [dict(r) for r in rows],
        "count": len(rows),
    }


def observe(
    project: str,
    query: str | None,
    scope: str,
    conn: sqlite3.Connection,
    config: dict[str, Any],
    session_id: str = "",
) -> dict[str, Any]:
    if query:
        return _observe_search(project, query, conn)

    if scope == "cluster":
        rows = conn.execute(
            "SELECT * FROM nodes WHERE project = ? ORDER BY activation DESC",
            (project,),
        ).fetchall()
        def _cluster_key(r: sqlite3.Row) -> str:
            label = r["label"] or r["id"]
            prefix = label.split()[0] if label else r["id"]
            act = r["activation"]
            if act > 0.7:
                bucket = "high"
            elif act > 0.3:
                bucket = "mid"
            else:
                bucket = "low"
            return f"{bucket}/{prefix}"
        clusters: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            clusters.setdefault(_cluster_key(r), []).append(r)
        return {
            "mode": "cluster",
            "clusters": {
                k: [dict(r) for r in members]
                for k, members in clusters.items()
            },
            "state_count": len(rows),
            "cluster_count": len(clusters),
        }

    if scope == "all":
        rows = conn.execute(
            "SELECT * FROM nodes WHERE project = ? ORDER BY activation DESC",
            (project,),
        ).fetchall()
        return {
            "mode": "all",
            "states": [dict(r) for r in rows],
            "count": len(rows),
        }

    if scope == "rank":
        iterations = config.get("page_rank", {}).get("iterations", 20)
        damping = config.get("page_rank", {}).get("damping", 0.85)
        pr = _compute_pagerank(project, conn, iterations, damping)
        rows = conn.execute(
            "SELECT * FROM nodes WHERE project = ?", (project,)
        ).fetchall()
        ranked = sorted(rows, key=lambda r: pr.get(r["id"], 0.0), reverse=True)
        return {
            "mode": "rank",
            "states": [dict(r) for r in ranked],
            "pagerank": {r["id"]: pr.get(r["id"], 0.0) for r in ranked},
            "count": len(ranked),
        }

    from openplan.core.activation import recompute_all_dirty
    recompute_all_dirty(conn, config)
    frontier = _get_frontier_states(project, conn, config)
    node_count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM nodes WHERE project = ?",
        (project,),
    ).fetchone()["cnt"]
    edge_count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ?",
        (project,),
    ).fetchone()["cnt"]
    density = (edge_count / (node_count * (node_count - 1))) if node_count > 1 else 0.0
    all_node_rows = conn.execute(
        "SELECT id FROM nodes WHERE project = ?", (project,)
    ).fetchall()
    edge_rows = conn.execute(
        "SELECT source_id, target_id FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ?",
        (project,),
    ).fetchall()

    global _path_length_cache
    if _path_length_cache is not None and _path_length_cache[1] == edge_count:
        avg_path_length = _path_length_cache[0]
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
        _path_length_cache = (avg_path_length, edge_count)

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

    recommended = max(
        frontier,
        key=lambda s: s["activation"] * (1.0 - _state_uncertainty(s["id"], conn)),
    )["id"] if frontier else None

    health = _graph_health(project, conn) if node_count > 0 else None

    return {
        "mode": "frontier",
        "states": [dict(s) for s in frontier],
        "graph": {
            "density": density,
            "avg_path_length": avg_path_length,
            "node_count": node_count,
            "edge_count": edge_count,
            "entropy": graph_entropy,
            "health": {
                "issues": health["issues"] if health else [],
                "orphan_count": health["orphan_count"] if health else 0,
                "calibration_count": health["calibration_count"] if health else 0,
                "action_types": len(health["actions_used"]) if health else 0,
            } if health and health["issues"] else None,
        },
        "recommended": recommended,
    }


def _actual_tokens(entry: dict) -> float:
    return entry["actual_cost"]["tokens"]


def _get_edge_cost(edge_data: dict[str, Any], config: dict[str, Any]) -> float:
    """Calculate effective edge cost, using learned adjustments when available."""
    raw_cost = edge_data["cost_tokens"]
    wh_raw = edge_data.get("weight_history") or "[]"
    try:
        weight_history = json.loads(wh_raw) if isinstance(wh_raw, str) else wh_raw
    except (json.JSONDecodeError, TypeError):
        weight_history = []

    learn_cfg = config.get("learning", {})
    smoothing = learn_cfg.get("smoothing_factor", 0.3)
    min_acts = learn_cfg.get("min_acts_for_calibration", 3)

    if len(weight_history) >= min_acts:
        actual_costs = [_actual_tokens(wh) for wh in weight_history]
        actual_avg = sum(actual_costs) / len(actual_costs)
        learned = smoothing * actual_avg + (1 - smoothing) * raw_cost
    else:
        learned = raw_cost

    return learned * (1 + edge_data["cost_risk"])


def _xml_escape(s: str) -> str:
    """Escape XML special characters."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")


def _compute_pagerank(
    project: str,
    conn: sqlite3.Connection,
    iterations: int = 20,
    damping: float = 0.85,
) -> dict[str, float]:
    """Compute PageRank scores for all nodes in a project."""
    nodes = conn.execute("SELECT id FROM nodes WHERE project = ?", (project,)).fetchall()
    n = len(nodes)
    if n == 0:
        return {}

    node_ids = [r["id"] for r in nodes]
    out_edges: dict[str, list[tuple[str, float]]] = {}
    for nid in node_ids:
        rows = conn.execute(
            "SELECT target_id, prob FROM edges WHERE source_id = ?", (nid,)
        ).fetchall()
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


def archive_events(
    conn: sqlite3.Connection,
    older_than_days: int = 30,
) -> dict[str, int]:
    """Archive events older than the specified threshold.

    Moves old events to the events_archive table and returns a summary
    of how many were archived.
    """

    cutoff = datetime.now(timezone.utc).timestamp() - older_than_days * 86400
    cutoff_str = datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    conn.execute(
        """INSERT OR IGNORE INTO events_archive
        SELECT * FROM events WHERE created_at < ?""",
        (cutoff_str,),
    )
    archived = conn.execute(
        "SELECT changes() AS cnt"
    ).fetchone()["cnt"]

    conn.execute("DELETE FROM events WHERE created_at < ?", (cutoff_str,))
    deleted = conn.execute("SELECT changes() AS cnt").fetchone()["cnt"]

    return {"archived": archived, "deleted": deleted}


def export(
    project: str,
    conn: sqlite3.Connection,
    fmt: str = "json",
) -> dict[str, Any]:
    if fmt == "matrix":
        edges = conn.execute(
            "SELECT e.* FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ?",
            (project,),
        ).fetchall()
        sparse = [
            {"source": e["source_id"], "target": e["target_id"],
             "value": e["cost_tokens"] * (1 + e["cost_risk"])}
            for e in edges
        ]
        return {
            "format": "matrix", "sparse": sparse,
            "project": project,
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }

    if fmt == "graphml":
        nodes = conn.execute(
            "SELECT * FROM nodes WHERE project = ?", (project,)
        ).fetchall()
        rows = conn.execute(
            "SELECT e.* FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ?",
            (project,),
        ).fetchall()
        parts: list[str] = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
            '  <key id="label" for="node" attr.name="label" attr.type="string"/>',
            '  <key id="activation" for="node" attr.name="activation" attr.type="double"/>',
            '  <key id="action" for="edge" attr.name="action" attr.type="string"/>',
            '  <key id="cost" for="edge" attr.name="cost" attr.type="double"/>',
            '  <key id="prob" for="edge" attr.name="probability" attr.type="double"/>',
            f'  <graph id="{project}" edgedefault="directed">',
        ]
        for r in nodes:
            parts.append(f'    <node id="{r["id"]}">')
            parts.append(f'      <data key="label">{_xml_escape(r["label"])}</data>')
            parts.append(f'      <data key="activation">{r["activation"]}</data>')
            parts.append("    </node>")
        for e in rows:
            parts.append(f'    <edge source="{e["source_id"]}" target="{e["target_id"]}">')
            parts.append(f'      <data key="action">{_xml_escape(e["action"])}</data>')
            parts.append(f'      <data key="cost">{e["cost_tokens"]}</data>')
            parts.append(f'      <data key="prob">{e["prob"]}</data>')
            parts.append("    </edge>")
        parts.append("  </graph>")
        parts.append("</graphml>")
        return {
            "format": "graphml",
            "graphml": "\n".join(parts),
            "project": project,
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }

    nodes = [dict(r) for r in conn.execute(
        "SELECT * FROM nodes WHERE project = ?", (project,)
    ).fetchall()]
    edges = [dict(r) for r in conn.execute(
        "SELECT e.* FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ?",
        (project,),
    ).fetchall()]
    events = [dict(r) for r in conn.execute(
        "SELECT * FROM events WHERE project = ?", (project,)
    ).fetchall()]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return {
        "nodes": nodes,
        "edges": edges,
        "events": events,
        "project": project,
        "exported_at": now,
        "version": "0.1.0",
    }


def branch(
    state_id: str,
    options: list[dict],
    conn: sqlite3.Connection,
    config: dict[str, Any],
    session_id: str = "",
) -> dict[str, Any]:
    src = conn.execute("SELECT * FROM nodes WHERE id = ?", (state_id,)).fetchone()
    if not src:
        return {"ok": False, "error": {"code": "INVALID_STATE", "message": f"State {state_id} not found"}}

    if not options:
        return {"ok": False, "error": {"code": "NO_OPTIONS", "message": "At least one option required"}}

    project = src["project"]
    now = _now()

    owned_branch = _safe_savepoint(conn, "branch_tx")
    try:
        branch_id = generate_branch_id(project, conn)
        states_created = []

        for opt in options:
            sid = generate_id(project, conn)
            label = opt.get("label", "")
            props = {"boost": True, "boosted_at": now}
            conn.execute(
                "INSERT INTO nodes (id, label, project, props, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (sid, label, project, json.dumps(props), now, now),
            )

            action = opt["action"]
            cost_tokens = opt.get("expected_cost", {}).get("tokens", 10000)
            cost_risk = opt.get("expected_cost", {}).get("risk", 0.1)
            prob = opt.get("prob", 0.8)

            conn.execute(
                "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (state_id, sid, action, cost_tokens, cost_risk, prob, now, now),
            )

            states_created.append(sid)

        payload = {
            "action": "branched",
            "source": state_id,
            "branch_id": branch_id,
            "options": len(options),
            "states_created": states_created,
        }
        _record_event(conn, state_id, project, "branched", payload, session_id)
        _safe_release(conn, "branch_tx", owned_branch)
    except Exception:
        _safe_rollback(conn, "branch_tx", owned_branch)
        raise

    mark_dirty(state_id, conn)
    _invalidate_path_cache()
    for s in states_created:
        increment_max_in_degree(s, conn)

    return {
        "ok": True,
        "branch_id": branch_id,
        "options": len(options),
        "states_created": states_created,
    }


def plan(
    from_id: str,
    target_id: str,
    conn: sqlite3.Connection,
    config: dict[str, Any],
    constraints: dict[str, Any] | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    import heapq
    import re

    src = conn.execute("SELECT * FROM nodes WHERE id = ?", (from_id,)).fetchone()
    if not src:
        return {"ok": False, "error": {"code": "INVALID_STATE", "message": f"From state {from_id} not found"}}

    resolved_state: str | None = None
    resolved_info: dict[str, Any] | None = None

    if re.match(r"^S-\d{6}$", target_id):
        tgt = conn.execute("SELECT * FROM nodes WHERE id = ?", (target_id,)).fetchone()
        if not tgt:
            return {"ok": False, "error": {"code": "INVALID_TARGET", "message": f"Target state {target_id} not found"}}
        resolved_state = target_id
    else:
        try:
            from openplan.core.embedding import get_cache, get_provider

            if not get_provider().loaded:
                return {
                    "ok": False,
                    "error": {
                        "code": "TARGET_RESOLUTION_FAILED",
                        "message": "Embedding provider not available — use a state ID instead",
                    },
                }
            cache = get_cache()
            results = cache.query(target_id, conn, top_k=1)
            if results:
                best = results[0]
                resolved_state = best["id"]
                resolved_info = best
            else:
                return {
                    "ok": False,
                    "error": {
                        "code": "TARGET_NOT_FOUND",
                        "message": f"Could not resolve '{target_id}' to any known state",
                    },
                }
        except Exception as exc:
            if "INVALID_STATE" in getattr(exc, "args", ()):
                raise
            return {
                "ok": False,
                "error": {
                    "code": "TARGET_RESOLUTION_FAILED",
                    "message": f"Failed to resolve target '{target_id}': {exc}",
                },
            }

    constraints = constraints or {}
    max_cost = constraints.get("max_cost")
    min_prob = constraints.get("min_prob")
    expansion_limit = constraints.get("expansion_limit", 500)
    avoid_states = set(constraints.get("avoid_states", []) or [])

    target_emb: np.ndarray | None = None
    avg_edge_cost = config.get("avg_edge_cost", 10000.0)
    embedding_cache = None
    _HEURISTIC_SCALE = config.get("heuristic_scale", 0.3)
    try:
        from openplan.core.embedding import get_cache as _get_emb_cache
        from openplan.core.embedding import get_provider as _get_emb_provider

        if _get_emb_provider().loaded:
            emb_cache = _get_emb_cache()
            emb_cache.refresh(conn)
            if emb_cache._matrix is not None and resolved_state in emb_cache._index:
                tgt_idx = emb_cache._index[resolved_state]
                target_emb = emb_cache._matrix[tgt_idx].copy()
                embedding_cache = emb_cache
    except Exception:
        pass

    def _heuristic(sid: str) -> float:
        if target_emb is None or embedding_cache is None:
            return 0.0
        idx = embedding_cache._index.get(sid)
        if idx is None:
            return 0.0
        state_emb = embedding_cache._matrix[idx]
        denom = np.linalg.norm(state_emb) * np.linalg.norm(target_emb)
        if denom == 0:
            return 0.0
        sim = float(np.dot(state_emb, target_emb) / denom)
        return (1.0 - sim) * avg_edge_cost * _HEURISTIC_SCALE

    f_start = _heuristic(from_id)
    pq: list = [(f_start, from_id, [from_id], 1.0, [], 0.0)]
    visited: dict[str, float] = {}
    expansions = 0
    candidates: list[tuple[float, list[str], float, list[dict[str, Any]]]] = []
    truncated = False

    while pq:
        f, node, path, cum_prob, edge_infos, g = heapq.heappop(pq)

        if node in visited and visited[node] <= g:
            continue
        visited[node] = g

        if max_cost is not None and g > max_cost:
            continue
        if min_prob is not None and cum_prob < min_prob:
            continue

        if node == resolved_state:
            candidates.append((g, path, cum_prob, edge_infos))
            continue

        expansions += 1
        if expansions > expansion_limit:
            truncated = True
            continue

        for e in conn.execute("SELECT * FROM edges WHERE source_id = ?", (node,)).fetchall():
            e_data = dict(e)
            neighbor = e_data["target_id"]

            if neighbor in avoid_states:
                continue

            edge_cost = _get_edge_cost(e_data, config)
            new_g = g + edge_cost
            new_prob = cum_prob * e_data["prob"]

            if max_cost is not None and new_g > max_cost:
                continue
            if min_prob is not None and new_prob < min_prob:
                continue

            if neighbor not in visited or visited[neighbor] > new_g:
                new_edge_infos = edge_infos + [
                    {
                        "from": node,
                        "action": e_data["action"],
                        "to": neighbor,
                        "prob": e_data["prob"],
                        "cost_tokens": e_data["cost_tokens"],
                        "cost_risk": e_data["cost_risk"],
                    }
                ]
                new_f = new_g + _heuristic(neighbor)
                heapq.heappush(pq, (new_f, neighbor, path + [neighbor], new_prob, new_edge_infos, new_g))

    if not candidates:
        if truncated:
            return {"ok": True, "path": None, "truncated": True, "error": "Expansion limit reached, no path found"}
        return {"ok": False, "error": {"code": "NO_PATH", "message": "No path found from source to target"}}

    candidates.sort(key=lambda x: x[0])
    top_paths: list[tuple[float, list[str], float, list[dict[str, Any]]]] = []
    for g_cost, p_path, p_prob, p_edges in candidates:
        if len(top_paths) >= 3:
            break
        too_similar = False
        for _, _, _, existing_edges in top_paths:
            shared = 0
            for e in p_edges:
                if e in existing_edges:
                    shared += 1
            max_shared = max(len(p_edges), len(existing_edges))
            if max_shared > 0 and (shared / max_shared) > 0.5:
                too_similar = True
                break
        if not too_similar:
            top_paths.append((g_cost, p_path, p_prob, p_edges))

    cost, path, cum_prob, edge_infos = top_paths[0]
    has_low_prob = any(ei["prob"] < 0.5 for ei in edge_infos)

    traversal = [
        {"from": ei["from"], "action": ei["action"], "to": ei["to"], "prob": ei["prob"]}
        for ei in edge_infos
    ]

    total_tokens = sum(ei["cost_tokens"] for ei in edge_infos)
    max_risk = max(ei["cost_risk"] for ei in edge_infos) if edge_infos else 0.0

    return {
        "ok": True,
        "path": path,
        "expected_cost": {
            "tokens": total_tokens,
            "risk": max_risk,
            "steps": len(path) - 1,
        },
        "traversal": traversal,
        "truncated": truncated,
        "high_uncertainty": has_low_prob,
    } | ({"resolved_target": resolved_info} if resolved_info else {})


def learn(
    from_state: str,
    to_state: str,
    outcome: str,
    actual_cost: float,
    conn: sqlite3.Connection,
    config: dict[str, Any],
    insight: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    """Calibrate edge costs and probability from a past transition.

    Args:
        from_state: Source state ID.
        to_state: Target state ID that was reached.
        outcome: "success" | "partial" | "failure" — how well the transition went.
        actual_cost: The actual cost tokens incurred.
        conn: Database connection.
        config: Configuration dict.
        insight: Optional free-form notes about what was learned.
    """
    if outcome not in ("success", "partial", "failure"):
        return {
            "ok": False,
            "error": {"code": "INVALID_OUTCOME", "message": f"Expected 'success', 'partial', or 'failure', got '{outcome}'"},
        }

    event = conn.execute(
        """SELECT * FROM events WHERE node_id = ? AND event_type = 'acted'
        AND json_extract(payload, '$.target') = ?
        ORDER BY created_at DESC LIMIT 1""",
        (from_state, to_state),
    ).fetchone()

    if not event:
        return {
            "ok": False,
            "error": {"code": "NO_EVENT", "message": f"No acted event found from {from_state} to {to_state}"},
        }

    try:
        payload = json.loads(event["payload"])
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "error": {"code": "INVALID_PAYLOAD", "message": "Event payload is not valid JSON"}}

    action = payload.get("action")
    if not action:
        return {"ok": False, "error": {"code": "NO_ACTION", "message": "Event payload missing action"}}

    expected_cost = payload.get("expected_cost")
    if expected_cost is None:
        expected_cost = {"tokens": actual_cost, "risk": 0.0}

    edge = conn.execute(
        "SELECT * FROM edges WHERE source_id = ? AND target_id = ? AND action = ?",
        (from_state, to_state, action),
    ).fetchone()

    if not edge:
        return {
            "ok": False,
            "error": {"code": "NO_EDGE", "message": f"No edge from {from_state} to {to_state} with action '{action}'"},
        }

    delta_tokens = actual_cost - expected_cost.get("tokens", actual_cost)

    src_node = conn.execute("SELECT project FROM nodes WHERE id = ?", (from_state,)).fetchone()
    project = src_node["project"] if src_node else "unknown"

    entry: dict[str, Any] = {
        "actual_cost": {"tokens": actual_cost},
        "expected_cost": expected_cost,
        "outcome": outcome,
        "delta": {"tokens": delta_tokens},
        "learned_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    if insight:
        entry["insight"] = insight

    try:
        wh = json.loads(edge["weight_history"]) if isinstance(edge["weight_history"], str) else (edge["weight_history"] or [])
    except (json.JSONDecodeError, TypeError):
        wh = []

    wh.append(entry)

    learn_cfg = config.get("learning", {})
    smoothing = learn_cfg.get("smoothing_factor", 0.3)
    min_acts = learn_cfg.get("min_acts_for_calibration", 3)

    if len(wh) >= min_acts:
        actual_avg = sum(_actual_tokens(w) for w in wh) / len(wh)
        new_cost = smoothing * actual_avg + (1 - smoothing) * edge["cost_tokens"]
    else:
        new_cost = edge["cost_tokens"]

    old_prob = edge["prob"]
    if outcome == "success":
        new_prob = min(1.0, old_prob * 1.1 + 0.05)
    elif outcome == "partial":
        new_prob = old_prob
    else:
        new_prob = max(0.01, old_prob * 0.7 - 0.1)

    old_activation = conn.execute(
        "SELECT activation FROM nodes WHERE id = ?", (from_state,)
    ).fetchone()
    old_act_val = old_activation["activation"] if old_activation else 0.0

    now = _now()
    owned_learn = _safe_savepoint(conn, "learn_edge_tx")
    try:
        conn.execute(
            "UPDATE edges SET weight_history = ?, cost_tokens = ?, prob = ?, updated_at = ? "
            "WHERE source_id = ? AND target_id = ? AND action = ?",
            (json.dumps(wh), new_cost, new_prob, now, from_state, to_state, action),
        )

        _record_event(conn, from_state, project, "calibrated", {
        "action": action,
        "from": from_state,
        "to": to_state,
        "outcome": outcome,
        "actual_cost": actual_cost,
        "previous_cost": edge["cost_tokens"],
        "new_cost": new_cost,
        "previous_prob": old_prob,
        "new_prob": new_prob,
    }, session_id)

        _safe_release(conn, "learn_edge_tx", owned_learn)
    except Exception:
        _safe_rollback(conn, "learn_edge_tx", owned_learn)
        raise

    try:
        from openplan.core.activation import get_activation

        mark_dirty(from_state, conn)
        new_activation = get_activation(from_state, conn, config)
    except Exception:
        new_activation = old_act_val

    embedding_shift = None

    return {
        "ok": True,
        "edge": {"from": from_state, "to": to_state, "action": action},
        "calibration": {
            "previous_cost": edge["cost_tokens"],
            "new_cost": new_cost,
            "previous_prob": old_prob,
            "new_prob": new_prob,
            "delta": delta_tokens,
            "history_length": len(wh),
        },
        "activation_shifts": [{"state": from_state, "delta": new_activation - old_act_val}],
        "embedding_shift": embedding_shift,
    }


def _graph_health(
    project: str,
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Shared health metrics: counts, orphan detection, depth, action diversity, calibration."""
    state_count = conn.execute(
        "SELECT COUNT(*) AS cnt FROM nodes WHERE project = ?", (project,)
    ).fetchone()["cnt"]
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
            "code": "EMPTY_GRAPH",
            "severity": "high",
            "message": "No edges exist",
            "fix": "Use branch() with action verbs, then act() to traverse",
        })
    elif len(actions_used) <= 1:
        issues.append({
            "code": "LOW_ACTION_DIVERSITY",
            "severity": "medium",
            "message": f"Only 1 action type used ({actions_used[0]['action']})",
            "fix": "Use domain verbs (implement, research, review, test, deploy) in branch() options",
        })
    if calibration_count == 0 and edge_count > 0:
        issues.append({
            "code": "NO_CALIBRATION",
            "severity": "high",
            "message": "No edges have been calibrated",
            "fix": "After each act(), call learn(from_state, to_state, outcome, actual_cost)",
        })
    if max_depth <= 1 and state_count > 3:
        issues.append({
            "code": "SHALLOW_GRAPH",
            "severity": "medium",
            "message": f"Graph depth is {max_depth}",
            "fix": "Branch again from the result state after acting, to build depth",
        })

    return {
        "state_count": state_count,
        "edge_count": edge_count,
        "max_depth": max_depth,
        "root_count": len(roots),
        "orphan_count": orphan_count,
        "orphans": orphans[:10],
        "calibration_count": calibration_count,
        "actions_used": actions_used,
        "issues": issues,
    }


def diagnostics(
    project: str,
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Return detailed graph health metrics for a project. Read-only."""
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
            "states": h["state_count"],
            "edges": h["edge_count"],
            "events": conn.execute("SELECT COUNT(*) AS cnt FROM events WHERE project = ?", (project,)).fetchone()["cnt"],
            "max_depth": h["max_depth"],
            "root_states": h["root_count"],
            "leaf_states": h["orphan_count"],
            "avg_out_degree": round(h["edge_count"] / h["state_count"], 2) if h["state_count"] > 0 else 0.0,
        },
        "health": {
            "calibrated_edges": h["calibration_count"],
            "calibration_rate": round(calibration_rate, 4),
            "action_types": len(h["actions_used"]),
        },
        "actions_used": h["actions_used"],
        "event_types": event_types,
        "orphans": h["orphans"],
        "orphan_count": h["orphan_count"],
        "issues": h["issues"],
    }


def project_list(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT n.project, n.id AS root_id, n.label, COUNT(DISTINCT e2.event_type) AS event_type_count, "
        "MAX(e.created_at) AS last_action "
        "FROM nodes n "
        "LEFT JOIN events e ON e.node_id = n.id AND e.event_type = 'acted' "
        "LEFT JOIN events e2 ON e2.project = n.project "
        "WHERE n.id IN (SELECT MIN(n2.id) FROM nodes n2 GROUP BY n2.project) "
        "GROUP BY n.project ORDER BY last_action DESC"
    ).fetchall()
    projects = {}
    for r in rows:
        projects[r["project"]] = {
            "root_id": r["root_id"],
            "label": r["label"],
            "event_types": r["event_type_count"],
            "last_action": r["last_action"],
        }
    return {
        "projects": list(projects.keys()),
        "roots": projects,
        "count": len(projects),
    }


def _project_root_node(project: str, conn: sqlite3.Connection) -> str | None:
    """Return the first (root) node ID for a project, or None if no nodes exist."""
    row = conn.execute(
        "SELECT id FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
        (project,),
    ).fetchone()
    return row["id"] if row else None


def compress(
    project: str,
    conn: sqlite3.Connection,
    config: dict[str, Any],
    older_than_days: int = 30,
    merge_orphans: bool = True,
    session_id: str = "",
) -> dict[str, Any]:
    """Archive old events and optionally merge orphan states."""
    owned = _safe_savepoint(conn, "compress_tx")
    try:
        now = _now()
        cutoff = datetime.now(timezone.utc).timestamp() - older_than_days * 86400
        cutoff_str = datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        conn.execute(
            """INSERT OR IGNORE INTO events_archive
            SELECT * FROM events WHERE project = ? AND created_at < ?""",
            (project, cutoff_str),
        )
        archived = conn.execute("SELECT changes() AS cnt").fetchone()["cnt"]
        conn.execute(
            "DELETE FROM events WHERE project = ? AND created_at < ?",
            (project, cutoff_str),
        )
        deleted = conn.execute("SELECT changes() AS cnt").fetchone()["cnt"]

        merged = 0
        if merge_orphans:
            from openplan.core.activation import get_activation
            all_states = conn.execute(
                "SELECT id FROM nodes WHERE project = ?", (project,)
            ).fetchall()
            for s in all_states:
                get_activation(s["id"], conn, config)
            orphans = conn.execute(
                "SELECT n.id, n.label FROM nodes n "
                "WHERE n.project = ? AND n.activation < 0.3 "
                "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.source_id = n.id) "
                "AND n.id NOT IN (SELECT MIN(n2.id) FROM nodes n2 WHERE n2.project = ?)",
                (project, project),
            ).fetchall()
            if orphans:
                parent = conn.execute(
                    "SELECT id FROM nodes WHERE project = ? ORDER BY activation DESC LIMIT 1",
                    (project,),
                ).fetchone()
                if parent:
                    orphan_ids = [o["id"] for o in orphans]
                    placeholders = ",".join("?" * len(orphan_ids))
                    conn.execute(
                        "INSERT OR IGNORE INTO edges (source_id, target_id, action, prob, created_at, updated_at) "
                        f"SELECT ?, id, 'merged', 1.0, ?, ? FROM nodes WHERE id IN ({placeholders})",
                        (parent["id"], now, now, *orphan_ids),
                    )
                    merged = len(orphan_ids)

        event_node = _project_root_node(project, conn)
        if event_node:
            _record_event(conn, event_node, project, "compressed", {
                "archived_events": archived,
                "deleted_events": deleted,
                "merged_orphans": merged,
            }, session_id)

        _safe_release(conn, "compress_tx", owned)
    except Exception:
        _safe_rollback(conn, "compress_tx", owned)
        raise

    return {
        "ok": True,
        "archived_events": archived,
        "deleted_events": deleted,
        "merged_orphans": merged,
    }
