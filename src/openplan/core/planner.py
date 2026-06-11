from __future__ import annotations

import heapq
import json
import re
import sqlite3
from typing import Any

import numpy as np

from openplan.core.activation import get_activation, mark_dirty
from openplan.core.state import _now, _record_event, _safe_release, _safe_rollback, _safe_savepoint


def _actual_tokens(entry: dict) -> float:
    return entry["actual_cost"]["tokens"]


def _get_edge_cost(edge_data: dict[str, Any], config: dict[str, Any]) -> float:
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


def plan(
    from_id: str,
    target_id: str,
    conn: sqlite3.Connection,
    config: dict[str, Any],
    constraints: dict[str, Any] | None = None,
    session_id: str = "",
) -> dict[str, Any]:
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
                return {"ok": False, "error": {"code": "TARGET_RESOLUTION_FAILED", "message": "Embedding provider not available — use a state ID instead"}}
            cache = get_cache()
            results = cache.query(target_id, conn, top_k=1)
            if results:
                best = results[0]
                resolved_state = best["id"]
                resolved_info = best
            else:
                return {"ok": False, "error": {"code": "TARGET_NOT_FOUND", "message": f"Could not resolve '{target_id}' to any known state"}}
        except Exception as exc:
            if "INVALID_STATE" in getattr(exc, "args", ()):
                raise
            return {"ok": False, "error": {"code": "TARGET_RESOLUTION_FAILED", "message": f"Failed to resolve target '{target_id}': {exc}"}}

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
                    {"from": node, "action": e_data["action"], "to": neighbor,
                     "prob": e_data["prob"], "cost_tokens": e_data["cost_tokens"], "cost_risk": e_data["cost_risk"]}
                ]
                heapq.heappush(pq, (new_g + _heuristic(neighbor), neighbor, path + [neighbor], new_prob, new_edge_infos, new_g))

    if not candidates:
        if truncated:
            return {"ok": True, "path": None, "truncated": True}
        return {"ok": False, "error": {"code": "NO_PATH", "message": "No path found from source to target"}}

    candidates.sort(key=lambda x: x[0])
    top_paths: list[tuple[float, list[str], float, list[dict[str, Any]]]] = []
    for g_cost, p_path, p_prob, p_edges in candidates:
        if len(top_paths) >= 3:
            break
        too_similar = False
        for _, _, _, existing_edges in top_paths:
            shared = sum(1 for e in p_edges if e in existing_edges)
            max_shared = max(len(p_edges), len(existing_edges))
            if max_shared > 0 and (shared / max_shared) > 0.5:
                too_similar = True
                break
        if not too_similar:
            top_paths.append((g_cost, p_path, p_prob, p_edges))

    cost, path, cum_prob, edge_infos = top_paths[0]
    has_low_prob = any(ei["prob"] < 0.5 for ei in edge_infos)
    traversal = [{"from": ei["from"], "action": ei["action"], "to": ei["to"], "prob": ei["prob"]} for ei in edge_infos]
    total_tokens = sum(ei["cost_tokens"] for ei in edge_infos)
    max_risk = max(ei["cost_risk"] for ei in edge_infos) if edge_infos else 0.0

    result: dict[str, Any] = {
        "ok": True, "path": path,
        "expected_cost": {"tokens": total_tokens, "risk": max_risk, "steps": len(path) - 1},
        "traversal": traversal, "truncated": truncated, "high_uncertainty": has_low_prob,
    }
    if resolved_info:
        result["resolved_target"] = resolved_info
    return result


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
    if outcome not in ("success", "partial", "failure"):
        return {"ok": False, "error": {"code": "INVALID_OUTCOME", "message": f"Expected 'success', 'partial', or 'failure', got '{outcome}'"}}

    event = conn.execute(
        """SELECT * FROM events WHERE node_id = ? AND event_type = 'acted'
        AND json_extract(payload, '$.target') = ? ORDER BY created_at DESC LIMIT 1""",
        (from_state, to_state),
    ).fetchone()
    if not event:
        return {"ok": False, "error": {"code": "NO_EVENT", "message": f"No acted event found from {from_state} to {to_state}"}}

    try:
        payload = json.loads(event["payload"])
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "error": {"code": "INVALID_PAYLOAD", "message": "Event payload is not valid JSON"}}

    action = payload.get("action")
    if not action:
        return {"ok": False, "error": {"code": "NO_ACTION", "message": "Event payload missing action"}}

    expected_cost = payload.get("expected_cost") or {"tokens": actual_cost, "risk": 0.0}
    edge = conn.execute(
        "SELECT * FROM edges WHERE source_id = ? AND target_id = ? AND action = ?",
        (from_state, to_state, action),
    ).fetchone()
    if not edge:
        return {"ok": False, "error": {"code": "NO_EDGE", "message": f"No edge from {from_state} to {to_state} with action '{action}'"}}

    delta_tokens = actual_cost - expected_cost.get("tokens", actual_cost)
    src_node = conn.execute("SELECT project FROM nodes WHERE id = ?", (from_state,)).fetchone()
    project = src_node["project"] if src_node else "unknown"

    entry: dict[str, Any] = {
        "actual_cost": {"tokens": actual_cost}, "expected_cost": expected_cost,
        "outcome": outcome, "delta": {"tokens": delta_tokens},
        "learned_at": _now(),
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
    new_cost = edge["cost_tokens"]
    if len(wh) >= min_acts:
        actual_avg = sum(_actual_tokens(w) for w in wh) / len(wh)
        new_cost = smoothing * actual_avg + (1 - smoothing) * edge["cost_tokens"]

    old_prob = edge["prob"]
    if outcome == "success":
        new_prob = min(1.0, old_prob * 1.1 + 0.05)
    elif outcome == "partial":
        new_prob = old_prob
    else:
        new_prob = max(0.01, old_prob * 0.7 - 0.1)

    old_activation = conn.execute("SELECT activation FROM nodes WHERE id = ?", (from_state,)).fetchone()
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
            "action": action, "from": from_state, "to": to_state,
            "outcome": outcome, "actual_cost": actual_cost,
            "previous_cost": edge["cost_tokens"], "new_cost": new_cost,
            "previous_prob": old_prob, "new_prob": new_prob,
        }, session_id)
        _safe_release(conn, "learn_edge_tx", owned_learn)
    except Exception:
        _safe_rollback(conn, "learn_edge_tx", owned_learn)
        raise

    try:
        mark_dirty(from_state, conn)
        new_activation = get_activation(from_state, conn, config)
    except Exception:
        new_activation = old_act_val

    return {
        "ok": True,
        "edge": {"from": from_state, "to": to_state, "action": action},
        "calibration": {
            "previous_cost": edge["cost_tokens"], "new_cost": new_cost,
            "previous_prob": old_prob, "new_prob": new_prob,
            "delta": delta_tokens, "history_length": len(wh),
        },
        "activation_shifts": [{"state": from_state, "delta": new_activation - old_act_val}],
        "embedding_shift": None,
    }
