from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any


class ActivationContext:
    def __init__(self) -> None:
        self.cache: dict[str, float] = {}
        self.dirty: set[str] = set()
        self.cache_order: list[str] = []
        self.visiting: set[str] = set()
        self.max_in_degree: int = 1
        self.validation_counter: int = 0
        self.VALIDATE_EVERY: int = 100
        self.max_in_degree_initialized: bool = False
        self.lock = threading.RLock()

    def increment_max_in_degree(self, target_id: str, conn: sqlite3.Connection) -> None:
        with self.lock:
            cnt = conn.execute(
                "SELECT COUNT(*) AS cnt FROM edges WHERE target_id = ?", (target_id,)
            ).fetchone()["cnt"]
            if cnt > self.max_in_degree:
                self.max_in_degree = cnt
            self.validation_counter += 1
            if self.validation_counter >= self.VALIDATE_EVERY:
                self._validate_max_in_degree(conn)

    def _validate_max_in_degree(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM edges GROUP BY target_id ORDER BY cnt DESC LIMIT 1"
        ).fetchone()
        self.max_in_degree = row["cnt"] if row else 1
        self.validation_counter = 0

    def _get_max_in_degree(self, conn: sqlite3.Connection) -> int:
        if not self.max_in_degree_initialized:
            self._validate_max_in_degree(conn)
            self.max_in_degree_initialized = True
        return self.max_in_degree

    def _get_outgoing_count(self, state_id: str, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM edges WHERE source_id = ?", (state_id,)
        ).fetchone()
        return row["cnt"]

    def _recompute_cache_order(self, conn: sqlite3.Connection) -> None:
        self.cache_order = sorted(
            self.dirty,
            key=lambda s: (
                self._get_outgoing_count(s, conn),
                -conn.execute(
                    "SELECT COUNT(*) AS cnt FROM edges WHERE target_id = ?", (s,)
                ).fetchone()["cnt"],
                s,
            ),
        )

    def mark_dirty(self, state_id: str, conn: sqlite3.Connection) -> None:
        with self.lock:
            self.dirty.add(state_id)
            preds = conn.execute(
                "SELECT DISTINCT source_id FROM edges WHERE target_id = ?", (state_id,)
            ).fetchall()
            for p in preds:
                self.dirty.add(p["source_id"])
            self._recompute_cache_order(conn)

    def _compute_frontier_ratio(self, state_id: str, conn: sqlite3.Connection, config: dict[str, Any]) -> float:
        threshold = config.get("activation_threshold", 0.5)
        edges = conn.execute(
            "SELECT target_id FROM edges WHERE source_id = ?", (state_id,)
        ).fetchall()
        if not edges:
            return 0.0
        active = 0
        for e in edges:
            target_act = self.cache.get(e["target_id"], 0.0)
            if target_act > threshold:
                active += 1
        return active / len(edges)

    def _compute_in_degree_ratio(self, state_id: str, conn: sqlite3.Connection) -> float:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM edges WHERE target_id = ?", (state_id,)
        ).fetchone()
        cnt = row["cnt"]
        max_in = self._get_max_in_degree(conn)
        return min(cnt / max_in, 1.0) if max_in > 0 else 0.0

    def _compute_recency(self, state_id: str, conn: sqlite3.Connection, stale_days: int) -> float:
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

    def _compute_agent_boost(self, state_id: str, conn: sqlite3.Connection) -> float:
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

    def _compute_activation(self, state_id: str, conn: sqlite3.Connection, config: dict[str, Any]) -> float:
        weights = config.get("activation_weights", {})
        w_in = weights.get("in_degree", 0.4)
        w_frontier = weights.get("frontier", 0.3)
        w_recency = weights.get("recency", 0.2)
        w_boost = weights.get("boost", 0.1)
        in_degree_ratio = self._compute_in_degree_ratio(state_id, conn)
        frontier_ratio = self._compute_frontier_ratio(state_id, conn, config)
        stale_days = config.get("stale_days", 2)
        recency = self._compute_recency(state_id, conn, stale_days)
        agent_boost = self._compute_agent_boost(state_id, conn)
        return w_in * in_degree_ratio + w_frontier * frontier_ratio + w_recency * recency + w_boost * agent_boost

    def _precompute_targets(self, state_id: str, conn: sqlite3.Connection, config: dict[str, Any]) -> None:
        stack = [(state_id, 0)]
        order: list[str] = []
        while stack:
            sid, idx = stack[-1]
            if idx == 0:
                if sid in self.visiting:
                    stack.pop()
                    continue
                self.visiting.add(sid)
                order.append(sid)
            targets = conn.execute(
                "SELECT target_id FROM edges WHERE source_id = ?", (sid,)
            ).fetchall()
            if idx < len(targets):
                tid = targets[idx]["target_id"]
                stack[-1] = (sid, idx + 1)
                if tid not in self.cache and tid not in self.visiting:
                    stack.append((tid, 0))
            else:
                stack.pop()
                self.visiting.discard(sid)
        for sid in reversed(order):
            if sid not in self.cache:
                act = self._compute_activation(sid, conn, config)
                self.cache[sid] = act
                threshold = config.get("activation_threshold", 0.5)
                frontier = 1 if self._is_frontier(sid, act, conn, threshold) else 0
                conn.execute(
                    "UPDATE nodes SET activation = ?, frontier = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                    (act, frontier, sid),
                )

    def _is_frontier(self, state_id: str, activation: float, conn: sqlite3.Connection, threshold: float) -> bool:
        if activation <= threshold:
            return False
        cnt = conn.execute(
            "SELECT COUNT(*) AS cnt FROM edges WHERE source_id = ?", (state_id,)
        ).fetchone()["cnt"]
        return cnt > 0

    def recompute_all_dirty(self, conn: sqlite3.Connection, config: dict[str, Any]) -> None:
        with self.lock:
            self._recompute_all_dirty_locked(conn, config)

    def _recompute_all_dirty_locked(self, conn: sqlite3.Connection, config: dict[str, Any]) -> None:
        threshold = config.get("activation_threshold", 0.5)
        for sid in self.cache_order:
            if sid in self.dirty:
                act = self._compute_activation(sid, conn, config)
                self.cache[sid] = act
                self.dirty.discard(sid)
                frontier = 1 if self._is_frontier(sid, act, conn, threshold) else 0
                conn.execute(
                    "UPDATE nodes SET activation = ?, frontier = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                    (act, frontier, sid),
                )

    def get_activation(self, state_id: str, conn: sqlite3.Connection, config: dict[str, Any]) -> float:
        with self.lock:
            if self.dirty:
                self._recompute_all_dirty_locked(conn, config)
            if state_id not in self.cache:
                self._precompute_targets(state_id, conn, config)
                act = self._compute_activation(state_id, conn, config)
                self.cache[state_id] = act
                threshold = config.get("activation_threshold", 0.5)
                frontier = 1 if self._is_frontier(state_id, act, conn, threshold) else 0
                conn.execute(
                    "UPDATE nodes SET activation = ?, frontier = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                    (act, frontier, state_id),
                )
            return self.cache[state_id]

    def reset(self) -> None:
        with self.lock:
            self.cache.clear()
            self.dirty.clear()
            self.cache_order.clear()
            self.visiting.clear()
            self.max_in_degree = 1
            self.validation_counter = 0
            self.max_in_degree_initialized = False


_default_ctx = ActivationContext()


def get_activation(state_id: str, conn: sqlite3.Connection, config: dict[str, Any], ctx: ActivationContext | None = None) -> float:
    return (ctx or _default_ctx).get_activation(state_id, conn, config)


def mark_dirty(state_id: str, conn: sqlite3.Connection, ctx: ActivationContext | None = None) -> None:
    (ctx or _default_ctx).mark_dirty(state_id, conn)


def recompute_all_dirty(conn: sqlite3.Connection, config: dict[str, Any], ctx: ActivationContext | None = None) -> None:
    (ctx or _default_ctx).recompute_all_dirty(conn, config)


def increment_max_in_degree(target_id: str, conn: sqlite3.Connection, ctx: ActivationContext | None = None) -> None:
    (ctx or _default_ctx).increment_max_in_degree(target_id, conn)


def reset_cache(ctx: ActivationContext | None = None) -> None:
    (ctx or _default_ctx).reset()
