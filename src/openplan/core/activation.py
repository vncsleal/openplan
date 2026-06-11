from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

_activation_cache: dict[str, float] = {}
_dirty_set: set[str] = set()
_cache_order: list[str] = []
_visiting: set[str] = set()
_max_in_degree: int = 1
_validation_counter: int = 0
_VALIDATE_EVERY: int = 100
_max_in_degree_initialized: bool = False
_activation_lock = threading.RLock()


def increment_max_in_degree(target_id: str, conn: sqlite3.Connection) -> None:
    """Called after edge insertion. Updates _max_in_degree if target_id now
    has more incoming edges than the current max."""
    global _max_in_degree, _validation_counter
    with _activation_lock:
        cnt = conn.execute(
            "SELECT COUNT(*) AS cnt FROM edges WHERE target_id = ?", (target_id,)
        ).fetchone()["cnt"]
        if cnt > _max_in_degree:
            _max_in_degree = cnt
        _validation_counter += 1
        if _validation_counter >= _VALIDATE_EVERY:
            _validate_max_in_degree(conn)


def _validate_max_in_degree(conn: sqlite3.Connection) -> None:
    """Full scan validation, runs periodically."""
    global _max_in_degree, _validation_counter
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM edges GROUP BY target_id ORDER BY cnt DESC LIMIT 1"
    ).fetchone()
    _max_in_degree = row["cnt"] if row else 1
    _validation_counter = 0


def _get_max_in_degree(conn: sqlite3.Connection) -> int:
    global _max_in_degree_initialized
    if not _max_in_degree_initialized:
        _validate_max_in_degree(conn)
        _max_in_degree_initialized = True
    return _max_in_degree


def _get_outgoing_count(state_id: str, conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM edges WHERE source_id = ?", (state_id,)
    ).fetchone()
    return row["cnt"]


def _recompute_cache_order(conn: sqlite3.Connection) -> None:
    global _cache_order
    _cache_order = sorted(
        _dirty_set,
        key=lambda s: (
            _get_outgoing_count(s, conn),
            -conn.execute(
                "SELECT COUNT(*) AS cnt FROM edges WHERE target_id = ?", (s,)
            ).fetchone()["cnt"],
            s,
        ),
    )


def mark_dirty(state_id: str, conn: sqlite3.Connection) -> None:
    with _activation_lock:
        _dirty_set.add(state_id)
        preds = conn.execute(
            "SELECT DISTINCT source_id FROM edges WHERE target_id = ?", (state_id,)
        ).fetchall()
        for p in preds:
            _dirty_set.add(p["source_id"])
        _recompute_cache_order(conn)


def _compute_frontier_ratio(state_id: str, conn: sqlite3.Connection, config: dict[str, Any]) -> float:
    threshold = config.get("activation_threshold", 0.5)
    edges = conn.execute(
        "SELECT target_id FROM edges WHERE source_id = ?", (state_id,)
    ).fetchall()
    if not edges:
        return 0.0
    active = 0
    for e in edges:
        target_act = _activation_cache.get(e["target_id"], 0.0)
        if target_act > threshold:
            active += 1
    return active / len(edges)


def _compute_in_degree_ratio(state_id: str, conn: sqlite3.Connection, max_in_degree: int) -> float:
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM edges WHERE target_id = ?", (state_id,)
    ).fetchone()
    cnt = row["cnt"]
    return min(cnt / max_in_degree, 1.0) if max_in_degree > 0 else 0.0


def _compute_recency(state_id: str, conn: sqlite3.Connection, stale_days: int) -> float:
    row = conn.execute(
        "SELECT updated_at FROM nodes WHERE id = ?", (state_id,)
    ).fetchone()
    if not row:
        return 0.0
    updated = row["updated_at"]
    try:
        updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0.0
    now = datetime.now(timezone.utc)
    if updated_dt.tzinfo is None:
        now = datetime.now()
    delta = now - updated_dt
    days = delta.total_seconds() / 86400.0
    return 1.0 - min(days / stale_days, 1.0)


def _compute_agent_boost(state_id: str, conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT props FROM nodes WHERE id = ?", (state_id,)
    ).fetchone()
    if not row:
        return 0.5
    try:
        props = json.loads(row["props"])
    except (json.JSONDecodeError, TypeError):
        return 0.5
    if props.get("boost") is True:
        boosted_at = props.get("boosted_at")
        if boosted_at:
            try:
                bt = datetime.fromisoformat(boosted_at)
                now = datetime.now(timezone.utc)
                if bt.tzinfo is None:
                    now = datetime.now()
                hours = (now - bt).total_seconds() / 3600.0
                if hours > 24:
                    return 0.5
            except (ValueError, TypeError):
                pass
        return 1.0
    return 0.5


def _compute_activation(state_id: str, conn: sqlite3.Connection, config: dict[str, Any]) -> float:
    weights = config.get("activation_weights", {})
    w_in = weights.get("in_degree", 0.4)
    w_frontier = weights.get("frontier", 0.3)
    w_recency = weights.get("recency", 0.2)
    w_boost = weights.get("boost", 0.1)

    max_in_degree = _get_max_in_degree(conn)
    in_degree_ratio = _compute_in_degree_ratio(state_id, conn, max_in_degree)
    frontier_ratio = _compute_frontier_ratio(state_id, conn, config)
    stale_days = config.get("stale_days", 2)
    recency = _compute_recency(state_id, conn, stale_days)
    agent_boost = _compute_agent_boost(state_id, conn)

    return (
        w_in * in_degree_ratio
        + w_frontier * frontier_ratio
        + w_recency * recency
        + w_boost * agent_boost
    )


def _precompute_targets(state_id: str, conn: sqlite3.Connection, config: dict[str, Any]) -> None:
    stack = [(state_id, 0)]
    order: list[str] = []
    while stack:
        sid, idx = stack[-1]
        if idx == 0:
            if sid in _visiting:
                stack.pop()
                continue
            _visiting.add(sid)
            order.append(sid)
        targets = conn.execute(
            "SELECT target_id FROM edges WHERE source_id = ?", (sid,)
        ).fetchall()
        if idx < len(targets):
            tid = targets[idx]["target_id"]
            stack[-1] = (sid, idx + 1)
            if tid not in _activation_cache and tid not in _visiting:
                stack.append((tid, 0))
        else:
            stack.pop()
            _visiting.discard(sid)
    for sid in reversed(order):
        if sid not in _activation_cache:
            act = _compute_activation(sid, conn, config)
            _activation_cache[sid] = act
            threshold = config.get("activation_threshold", 0.5)
            frontier = 1 if _is_frontier(sid, act, conn, threshold) else 0
            conn.execute(
                "UPDATE nodes SET activation = ?, frontier = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (act, frontier, sid),
            )


def _is_frontier(state_id: str, activation: float, conn: sqlite3.Connection, threshold: float) -> bool:
    """A state is a frontier if activation > threshold AND it has outgoing edges."""
    if activation <= threshold:
        return False
    cnt = conn.execute(
        "SELECT COUNT(*) AS cnt FROM edges WHERE source_id = ?", (state_id,)
    ).fetchone()["cnt"]
    return cnt > 0


def recompute_all_dirty(conn: sqlite3.Connection, config: dict[str, Any]) -> None:
    with _activation_lock:
        _recompute_all_dirty_locked(conn, config)


def _recompute_all_dirty_locked(conn: sqlite3.Connection, config: dict[str, Any]) -> None:
    threshold = config.get("activation_threshold", 0.5)
    for sid in _cache_order:
        if sid in _dirty_set:
            act = _compute_activation(sid, conn, config)
            _activation_cache[sid] = act
            _dirty_set.discard(sid)
            frontier = 1 if _is_frontier(sid, act, conn, threshold) else 0
            conn.execute(
                "UPDATE nodes SET activation = ?, frontier = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (act, frontier, sid),
            )


def get_activation(state_id: str, conn: sqlite3.Connection, config: dict[str, Any]) -> float:
    with _activation_lock:
        if _dirty_set:
            _recompute_all_dirty_locked(conn, config)
        if state_id not in _activation_cache:
            _precompute_targets(state_id, conn, config)
            act = _compute_activation(state_id, conn, config)
            _activation_cache[state_id] = act
            threshold = config.get("activation_threshold", 0.5)
            frontier = 1 if _is_frontier(state_id, act, conn, threshold) else 0
            conn.execute(
                "UPDATE nodes SET activation = ?, frontier = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (act, frontier, state_id),
            )
        return _activation_cache[state_id]


def reset_cache() -> None:
    with _activation_lock:
        _activation_cache.clear()
        _dirty_set.clear()
        _cache_order.clear()
        _visiting.clear()
        global _max_in_degree, _validation_counter, _max_in_degree_initialized
        _max_in_degree = 1
        _validation_counter = 0
        _max_in_degree_initialized = False
