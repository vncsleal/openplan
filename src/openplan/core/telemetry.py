from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, deque
from typing import Any

_log = logging.getLogger("openplan.telemetry")

EVENTS_TABLE = "calibration_events"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {EVENTS_TABLE} (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_type  TEXT NOT NULL DEFAULT '',
    action        TEXT NOT NULL,
    expected_cost REAL,
    actual_cost   REAL NOT NULL,
    outcome       TEXT NOT NULL DEFAULT 'success',
    session_id    TEXT NOT NULL DEFAULT '',
    synced        INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cal_events_synced ON {EVENTS_TABLE}(synced);
CREATE INDEX IF NOT EXISTS idx_cal_events_lookup ON {EVENTS_TABLE}(project_type, action);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def capture(conn: sqlite3.Connection, project_type: str, action: str, actual_cost: float, expected_cost: float | None = None, outcome: str = "success") -> None:
    if os.environ.get("OPENPLAN_NO_TELEMETRY"):
        return
    try:
        conn.execute(
            f"INSERT INTO {EVENTS_TABLE} (project_type, action, expected_cost, actual_cost, outcome, session_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_type or "", action, expected_cost, actual_cost, outcome, os.environ.get("OPENCODE_SESSION_ID", ""), time.time()),
        )
    except Exception:
        _log.debug("Telemetry capture failed (non-blocking)")


def get_local_calibration(conn: sqlite3.Connection, min_samples: int = 3) -> list[dict[str, Any]]:
    rows = conn.execute(f"""
        SELECT project_type, action, COUNT(*) AS sample_count, AVG(actual_cost) AS cost_tokens
        FROM {EVENTS_TABLE}
        WHERE actual_cost IS NOT NULL AND actual_cost > 0
        GROUP BY project_type, action
        HAVING sample_count >= ?
        ORDER BY sample_count DESC
    """, (min_samples,)).fetchall()
    return [
        {"project_type": r["project_type"], "action": r["action"],
         "cost_tokens": round(r["cost_tokens"], 2), "sample_count": r["sample_count"]}
        for r in rows
    ]


def import_global_calibration(conn: sqlite3.Connection, endpoint: str) -> int:
    if not endpoint:
        return 0
    try:
        req = urllib.request.Request(f"{endpoint.rstrip('/')}/calibration")
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        _log.debug("Calibration fetch failed (non-blocking)")
        return 0

    baselines = data if isinstance(data, list) else data.get("baselines", [])
    now = time.strftime("%Y-%m-%dT%H:%M:%S.%fZ", time.gmtime())
    count = 0
    for bl in baselines:
        pt = bl.get("project_type", "")
        action = bl.get("action", "")
        cost = bl.get("cost_tokens")
        samples = bl.get("sample_count", 1)
        if not action or not cost:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO cost_baselines (project, project_type, action, cost_tokens, cost_risk, sample_count, updated_at) "
            "VALUES (NULL, ?, ?, ?, 0.1, ?, ?)",
            (pt, action, cost, samples, now),
        )
        count += 1
    if count:
        conn.commit()
        _log.info("Imported %d global calibration baselines", count)
    return count


class TelemetryTracker:
    """Tracks tool usage patterns within a session for self-tuning."""
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, deque[tuple[str, dict[str, Any]]]] = {}
        self._last_suggestion: dict[str, dict[str, Any]] = {}
        self._suggestion_hits: dict[str, dict[str, dict[str, int]]] = {}
        self._window = 50
        self._conn: sqlite3.Connection | None = None

    def record(self, session_id: str, tool: str, args: dict[str, Any] | None = None) -> None:
        sid = session_id or "_default"
        with self._lock:
            if sid not in self._sessions:
                self._sessions[sid] = deque(maxlen=self._window)
                self._suggestion_hits[sid] = {}
            self._sessions[sid].append((tool, args or {}))
            self._check_suggestion_match(sid, tool)

    def record_suggestion(self, session_id: str, suggestion: dict[str, Any]) -> None:
        sid = session_id or "_default"
        with self._lock:
            self._last_suggestion[sid] = suggestion

    _LOOP_TRANSITIONS: dict[str, set[str]] = {
        "act": {"learn", "act"},
        "learn": {"observe", "learn"},
        "observe": {"branch", "act", "observe"},
        "branch": {"act", "branch"},
        "plan": {"branch", "observe", "plan"},
    }

    def _check_suggestion_match(self, sid: str, tool: str) -> None:
        suggestion = self._last_suggestion.get(sid)
        suggested_tool = suggestion.get("tool") if suggestion else None
        if suggested_tool:
            record = self._suggestion_hits[sid].setdefault(suggested_tool, {"followed": 0, "ignored": 0})
            valid = self._LOOP_TRANSITIONS.get(suggested_tool, {suggested_tool})
            if tool in valid:
                record["followed"] += 1
            else:
                record["ignored"] += 1

    def get_session_stats(self, session_id: str) -> dict[str, Any]:
        sid = session_id or "_default"
        if sid not in self._sessions:
            return {"calls": 0}
        with self._lock:
            calls = list(self._sessions[sid])
            tool_counts = Counter(t[0] for t in calls)
            recent = [t[0] for t in calls[-10:]]
            repeated_observes = 0
            for t in recent:
                if t == "observe":
                    repeated_observes += 1
                else:
                    repeated_observes = 0
            stuck = repeated_observes >= 3
            total_followed = sum(rec["followed"] for rec in self._suggestion_hits.get(sid, {}).values())
            total_ignored = sum(rec["ignored"] for rec in self._suggestion_hits.get(sid, {}).values())
            total_suggestions = total_followed + total_ignored
            result: dict[str, Any] = {"calls": len(calls), "tool_counts": dict(tool_counts.most_common(5))}
            if stuck:
                result["stuck"] = True
                result["stuck_detail"] = "observe called 3+ times without act"
            if total_suggestions > 0:
                result["suggestion_conversion"] = {
                    "followed": total_followed, "ignored": total_ignored,
                    "rate": round(total_followed / total_suggestions, 2),
                }
            return result

    def get_suggestion_conversion(self, session_id: str) -> dict[str, Any] | None:
        sid = session_id or "_default"
        if sid not in self._suggestion_hits:
            return None
        with self._lock:
            total_followed = sum(rec["followed"] for rec in self._suggestion_hits[sid].values())
            total_ignored = sum(rec["ignored"] for rec in self._suggestion_hits[sid].values())
            total = total_followed + total_ignored
            return {"followed": total_followed, "ignored": total_ignored,
                    "rate": round(total_followed / total, 2)} if total > 0 else None

    def get_global_conversion_rate(self) -> float | None:
        with self._lock:
            total_f = sum(r["followed"] for rec in self._suggestion_hits.values() for r in rec.values())
            total_i = sum(r["ignored"] for rec in self._suggestion_hits.values() for r in rec.values())
            total = total_f + total_i
            return total_f / total if total > 0 else None

    def get_tool_conversion_rate(self, tool: str) -> float | None:
        with self._lock:
            total_f = sum(rec.get(tool, {}).get("followed", 0) for rec in self._suggestion_hits.values())
            total_i = sum(rec.get(tool, {}).get("ignored", 0) for rec in self._suggestion_hits.values())
            total = total_f + total_i
            return total_f / total if total > 0 else None

    def set_conn(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def flush_to_events(self) -> None:
        if not self._conn:
            return
        with self._lock:
            for sid, hits in self._suggestion_hits.items():
                for tool, rec in hits.items():
                    if rec["followed"] + rec["ignored"] == 0:
                        continue
                    digest = hashlib.sha256(f"telemetry:{sid}:{tool}".encode()).hexdigest()
                    eid = int(digest[:12], 16) % 1000000
                    self._conn.execute(
                        "INSERT OR IGNORE INTO events (id, project, node_id, event_type, payload, version, idempotency_key, session_id, created_at) "
                        "VALUES (?, '__telemetry__', '__telemetry__', 'telemetry', ?, 1, ?, '', '')",
                        (f"E-{eid:06d}", json.dumps({"session": sid, "tool": tool, "followed": rec["followed"], "ignored": rec["ignored"]}), digest[:32]),
                    )
            if self._conn:
                self._conn.commit()

    def reload_from_events(self) -> None:
        if not self._conn:
            return
        with self._lock:
            try:
                for r in self._conn.execute(
                    "SELECT payload FROM events WHERE event_type = 'telemetry'"
                ).fetchall():
                    payload = json.loads(r["payload"])
                    sid = payload["session"]
                    if sid not in self._suggestion_hits:
                        self._suggestion_hits[sid] = {}
                    self._suggestion_hits[sid][payload["tool"]] = {
                        "followed": payload["followed"], "ignored": payload["ignored"],
                    }
            except Exception:
                _log.exception("Failed to reload telemetry from events")


def sync_to_endpoint(conn: sqlite3.Connection, endpoint: str, batch_size: int = 50) -> int:
    if not endpoint:
        return 0
    endpoint = endpoint.rstrip("/")
    total = 0
    while True:
        rows = conn.execute(
            f"SELECT id, project_type, action, expected_cost, actual_cost, outcome, session_id, created_at FROM {EVENTS_TABLE} WHERE synced = 0 ORDER BY id LIMIT ?",
            (batch_size,),
        ).fetchall()
        if not rows:
            break
        events = [
            {"project_type": r["project_type"], "action": r["action"],
             "expected_cost": r["expected_cost"], "actual_cost": r["actual_cost"],
             "outcome": r["outcome"], "session_id": r["session_id"],
             "timestamp": r["created_at"]}
            for r in rows
        ]
        ids = [r["id"] for r in rows]
        try:
            payload = json.dumps({"events": events}).encode("utf-8")
            req = urllib.request.Request(
                f"{endpoint}/telemetry", data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
            conn.execute(
                f"UPDATE {EVENTS_TABLE} SET synced = 1 WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )
            conn.commit()
            total += len(events)
        except Exception:
            _log.debug("Telemetry sync failed (non-blocking)")
            break
    return total


_telemetry = TelemetryTracker()


def get_telemetry() -> TelemetryTracker:
    return _telemetry
