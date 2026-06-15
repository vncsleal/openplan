from __future__ import annotations

import heapq
import json
import math
import re
import sqlite3
from typing import Any

import numpy as np

from openplan.core.activation import get_activation, mark_dirty
from openplan.core.errors import (
    InvalidActionError, InvalidOutcomeError, InvalidPayloadError, InvalidStateError,
    NoActionError, NoEdgeError, NoEventError, NoPathError, OpenPlanError,
    TargetNotFoundError, TargetResolutionError,
)
from openplan.core.state import _now, _record_event, _safe_release, _safe_rollback, _safe_savepoint


def _actual_tokens(entry: dict) -> float:
    return entry["actual_cost"]["tokens"]


def _get_predictive_cost(action: str, project: str, project_type: str, conn: sqlite3.Connection) -> float | None:
    if project:
        row = conn.execute(
            "SELECT cost_tokens, cost_risk FROM cost_baselines WHERE project = ? AND action = ?",
            (project, action),
        ).fetchone()
        if row:
            return row["cost_tokens"] * (1 + row["cost_risk"])
    if project_type:
        row = conn.execute(
            "SELECT cost_tokens, cost_risk FROM cost_baselines WHERE project IS NULL AND project_type = ? AND action = ?",
            (project_type, action),
        ).fetchone()
        if row:
            return row["cost_tokens"] * (1 + row["cost_risk"])
    return None


def _meets_preconditions(edge_data: dict[str, Any], conn: sqlite3.Connection, config: dict[str, Any] | None = None) -> bool:
    raw = edge_data.get("conditions", "")
    if not raw:
        return True
    try:
        conditions = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return True
    if not isinstance(conditions, list):
        return True
    source_id = edge_data["source_id"]
    node = conn.execute("SELECT props FROM nodes WHERE id = ?", (source_id,)).fetchone()
    if not node:
        return False
    try:
        props = json.loads(node["props"]) if isinstance(node["props"], str) else node["props"]
    except (json.JSONDecodeError, TypeError):
        return False
    for cond in conditions:
        if isinstance(cond, dict):
            field = cond.get("field", "")
            expected = cond.get("value")
            if props.get(field) != expected:
                return False
    return True


def _get_edge_cost(edge_data: dict[str, Any], config: dict[str, Any], conn: sqlite3.Connection | None = None) -> float:
    raw_cost = edge_data["cost_tokens"]
    if conn is not None:
        pred = _get_predictive_cost(
            edge_data.get("action", ""),
            edge_data.get("project", ""),
            config.get("project_type", ""),
            conn,
        )
        if pred is not None:
            raw_cost = pred
    wh_raw = edge_data.get("weight_history") or "[]"
    try:
        weight_history = json.loads(wh_raw) if isinstance(wh_raw, str) else wh_raw
    except (json.JSONDecodeError, TypeError):
        weight_history = []

    real_entries = [wh for wh in weight_history if not wh.get("auto")]
    learn_cfg = config.get("learning", {})
    smoothing = learn_cfg.get("smoothing_factor", 0.3)
    min_acts = learn_cfg.get("min_acts_for_calibration", 1)

    learned = raw_cost
    if len(real_entries) >= min_acts:
        actual_costs = [_actual_tokens(wh) for wh in real_entries]
        actual_avg = sum(actual_costs) / len(actual_costs)
        learned = smoothing * actual_avg + (1 - smoothing) * raw_cost

    effective_cost = learned * (1 + edge_data["cost_risk"])
    return effective_cost


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
        raise InvalidStateError(from_id)

    resolved_state: str | None = None
    resolved_info: dict[str, Any] | None = None

    if re.match(r"^S-\d{6}$", target_id):
        tgt = conn.execute("SELECT * FROM nodes WHERE id = ?", (target_id,)).fetchone()
        if not tgt:
            raise TargetNotFoundError(from_id, "resolve", target_id)
        resolved_state = target_id
    else:
        from openplan.core.resolve import resolve_target

        project_row = conn.execute(
            "SELECT project FROM nodes WHERE id = ?", (from_id,)
        ).fetchone()
        project = project_row["project"] if project_row else None
        if not project:
            raise TargetResolutionError("Could not determine project from source state")
        results = resolve_target(target_id, project, conn, top_k=1)
        if results:
            best = results[0]
            resolved_state = best["id"]
            resolved_info = best
        else:
            raise TargetResolutionError(f"Could not resolve '{target_id}' to any known state")

    constraints = constraints or {}
    max_cost = constraints.get("max_cost")
    min_prob = constraints.get("min_prob")
    expansion_limit = constraints.get("expansion_limit", 500)
    top_k = constraints.get("top_k", 1)
    avoid_states = set(constraints.get("avoid_states", []) or [])
    risk_adjustment = constraints.get("risk_adjustment") or config.get("risk_adjustment", "probability")

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
            target_emb = emb_cache.get_embedding(resolved_state) if resolved_state else None
            embedding_cache = emb_cache
    except Exception:
        pass

    tuning_data: dict[str, dict] = {}
    for r in conn.execute("SELECT key, value FROM meta WHERE key LIKE 'tuning:%'"):
        action = r["key"][7:]
        try:
            tuning_data[action] = json.loads(r["value"])
        except (json.JSONDecodeError, TypeError):
            pass
    self_tune_overrides: dict[str, Any] = {}
    st_row = conn.execute(
        "SELECT value FROM meta WHERE key = 'self_tuning:overrides'"
    ).fetchone()
    if st_row:
        try:
            self_tune_overrides = json.loads(st_row["value"])
        except (json.JSONDecodeError, TypeError):
            pass

    if src:
        for r in conn.execute(
            "SELECT id FROM nodes WHERE project = ? AND status IN ('blocked', 'cascade_blocked')",
            (src["project"],),
        ):
            if r["id"] != resolved_state:
                for sub in conn.execute(
                    "SELECT target_id FROM edges WHERE source_id = ?", (r["id"],)
                ):
                    if sub["target_id"] != resolved_state:
                        avoid_states.add(sub["target_id"])

    resolved_label = ""
    if resolved_state:
        row = conn.execute("SELECT label FROM nodes WHERE id = ?", (resolved_state,)).fetchone()
        if row:
            resolved_label = row["label"] or ""
    target_tokens = set(re.findall(r"[a-zA-Z0-9_]+", resolved_label.lower())) if resolved_label else set()

    goal_text = ""
    if src:
        gr = conn.execute("SELECT value FROM meta WHERE key = ?", (f"goal:{src['project']}",)).fetchone()
        if gr:
            try:
                gv = json.loads(gr["value"])
                if isinstance(gv, dict):
                    goal_text = gv.get("text", "")
            except (json.JSONDecodeError, TypeError):
                pass
    goal_tokens = set(re.findall(r"[a-zA-Z0-9_]+", goal_text.lower())) if goal_text else set()

    heuristic_method = "none"

    def _heuristic(sid: str) -> float:
        nonlocal heuristic_method
        if target_emb is not None and embedding_cache is not None:
            state_emb = embedding_cache.get_embedding(sid)
            if state_emb is not None:
                denom = np.linalg.norm(state_emb) * np.linalg.norm(target_emb)
                if denom != 0:
                    sim = float(np.dot(state_emb, target_emb) / denom)
                    heuristic_method = "embedding"
                    return (1.0 - sim) * avg_edge_cost * _HEURISTIC_SCALE
        sr = conn.execute("SELECT label FROM nodes WHERE id = ?", (sid,)).fetchone()
        if sr and sr["label"]:
            state_tokens = set(re.findall(r"[a-zA-Z0-9_]+", sr["label"].lower()))
            overlap = target_tokens & state_tokens
            if target_tokens:
                sim = len(overlap) / len(target_tokens)
                heuristic_method = "token"
                return (1.0 - sim) * avg_edge_cost * _HEURISTIC_SCALE * 0.5
        if goal_tokens and sr and sr["label"]:
            state_tokens = set(re.findall(r"[a-zA-Z0-9_]+", sr["label"].lower()))
            g_overlap = goal_tokens & state_tokens
            if goal_tokens:
                g_sim = len(g_overlap) / len(goal_tokens)
                heuristic_method = "goal"
                return (1.0 - g_sim) * avg_edge_cost * _HEURISTIC_SCALE * 0.3
        heuristic_method = "zero"
        return 0.0

    def _adjusted_cost(prob: float, base_cost: float) -> float:
        if risk_adjustment == "probability":
            return base_cost / max(prob, 0.01)
        elif risk_adjustment == "variance":
            return base_cost * (1 + (1 - prob) ** 2)
        return base_cost

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
            if not _meets_preconditions(e_data, conn, config):
                continue
            edge_cost = _get_edge_cost(e_data, config, conn)
            edge_prob = e_data["prob"]
            action_name = e_data.get("action", "")
            action_penalties = self_tune_overrides.get("action_penalties", {})
            penalty = action_penalties.get(action_name, 1.0)
            edge_cost *= penalty
            if action_name in tuning_data:
                td = tuning_data[e_data["action"]]
                sr = td.get("success_rate", 0.5)
                if sr < 0.3:
                    edge_cost *= 1.5
                elif sr > 0.8:
                    edge_cost *= 0.8
            adjusted_cost = _adjusted_cost(edge_prob, edge_cost)
            new_g = g + adjusted_cost
            new_prob = cum_prob * edge_prob
            if max_cost is not None and new_g > max_cost:
                continue
            if min_prob is not None and new_prob < min_prob:
                continue
            if neighbor not in visited or visited[neighbor] > new_g:
                new_edge_infos = edge_infos + [
                    {"from": node, "action": e_data["action"], "to": neighbor,
                     "prob": edge_prob, "cost_tokens": e_data["cost_tokens"], "cost_risk": e_data["cost_risk"],
                     "effective_cost": round(adjusted_cost, 1)}
                ]
                heapq.heappush(pq, (new_g + _heuristic(neighbor), neighbor, path + [neighbor], new_prob, new_edge_infos, new_g))

        reverse_penalty = config.get("reverse_penalty", 1.0)
        for e in conn.execute("SELECT * FROM edges WHERE target_id = ?", (node,)).fetchall():
            e_data = dict(e)
            neighbor = e_data["source_id"]
            if neighbor in avoid_states:
                continue
            if not _meets_preconditions(e_data, conn, config):
                continue
            edge_cost = _get_edge_cost(e_data, config, conn) * reverse_penalty
            edge_prob = e_data["prob"]
            action_penalties = self_tune_overrides.get("action_penalties", {})
            edge_cost *= action_penalties.get(e_data.get("action", ""), 1.0)
            adjusted_cost = _adjusted_cost(edge_prob, edge_cost)
            new_g = g + adjusted_cost
            new_prob = cum_prob * edge_prob
            if max_cost is not None and new_g > max_cost:
                continue
            if min_prob is not None and new_prob < min_prob:
                continue
            if neighbor not in visited or visited[neighbor] > new_g:
                new_edge_infos = edge_infos + [
                    {"from": node, "action": f"reverse({e_data['action']})", "to": neighbor, "direction": "reverse",
                     "prob": edge_prob, "cost_tokens": e_data["cost_tokens"], "cost_risk": e_data["cost_risk"],
                     "effective_cost": round(adjusted_cost, 1)}
                ]
                heapq.heappush(pq, (new_g + _heuristic(neighbor), neighbor, path + [neighbor], new_prob, new_edge_infos, new_g))

    if not candidates:
        if truncated:
            return {"ok": True, "path": None, "truncated": True}
        raise NoPathError()

    candidates.sort(key=lambda x: x[0])
    top_k = max(1, min(top_k, 5))
    top_paths = candidates[:top_k]

    cost, path, cum_prob, edge_infos = top_paths[0]
    has_low_prob = any(ei["prob"] < 0.5 for ei in edge_infos)
    traversal = []
    total_variance = 0.0
    for ei in edge_infos:
        entry = {"from": ei["from"], "action": ei["action"], "to": ei["to"], "prob": ei["prob"], "direction": ei.get("direction", "forward")}
        if "effective_cost" in ei:
            entry["effective_cost"] = ei["effective_cost"]
        action = ei.get("action", "").replace("reverse(", "").rstrip(")")
        td = tuning_data.get(action, {})
        if td.get("cost_variance") is not None:
            entry["cost_variance"] = td["cost_variance"]
            total_variance += td["cost_variance"]
        if td.get("cost_stddev") is not None:
            entry["cost_stddev"] = td["cost_stddev"]
        if td.get("cost_ci_95") is not None:
            entry["cost_ci_95"] = td["cost_ci_95"]
        traversal.append(entry)

    total_tokens = sum(ei["cost_tokens"] for ei in edge_infos)
    max_risk = max(ei["cost_risk"] for ei in edge_infos) if edge_infos else 0.0

    expected_cost: dict[str, Any] = {"tokens": total_tokens, "risk": max_risk, "prob": cum_prob, "steps": len(path) - 1}
    if total_variance > 0:
        expected_cost["cost_variance"] = round(total_variance, 1)
        stddev = math.sqrt(total_variance)
        expected_cost["cost_stddev"] = round(stddev, 1)
        expected_cost["confidence_interval"] = [
            round(max(0, total_tokens - 1.96 * stddev), 1),
            round(total_tokens + 1.96 * stddev, 1),
        ]

    result: dict[str, Any] = {
        "ok": True, "path": path,
        "expected_cost": expected_cost,
        "traversal": traversal, "truncated": truncated, "high_uncertainty": has_low_prob,
        "heuristic_method": heuristic_method, "risk_adjustment": risk_adjustment,
    }
    if resolved_info:
        result["resolved_target"] = resolved_info
    if len(top_paths) > 1:
        result["alternatives"] = [
            {
                "path": p,
                "expected_cost": {"tokens": sum(ei["cost_tokens"] for ei in es), "prob": pp, "steps": len(p) - 1},
                "traversal": [{"from": ei["from"], "action": ei["action"], "to": ei["to"], "prob": ei["prob"], "effective_cost": ei.get("effective_cost")} for ei in es],
            }
            for gc, p, pp, es in top_paths[1:]
        ]
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
        raise InvalidOutcomeError(outcome)

    event = conn.execute(
        """SELECT * FROM events WHERE node_id = ? AND event_type = 'acted'
        AND json_extract(payload, '$.target') = ? ORDER BY created_at DESC LIMIT 1""",
        (from_state, to_state),
    ).fetchone()
    if not event:
        raise NoEventError(from_state, to_state)

    try:
        payload = json.loads(event["payload"])
    except (json.JSONDecodeError, TypeError):
        raise InvalidPayloadError()

    action = payload.get("action")
    if not action:
        raise NoActionError()

    expected_cost = payload.get("expected_cost") or {"tokens": actual_cost, "risk": 0.0}
    edge = conn.execute(
        "SELECT * FROM edges WHERE source_id = ? AND target_id = ? AND action = ?",
        (from_state, to_state, action),
    ).fetchone()
    if not edge:
        raise NoEdgeError(from_state, to_state, action)

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
    min_acts = learn_cfg.get("min_acts_for_calibration", 1)
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
    except OpenPlanError:
        _safe_rollback(conn, "learn_edge_tx", owned_learn)
        raise
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
