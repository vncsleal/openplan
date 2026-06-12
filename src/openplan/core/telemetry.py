from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from collections import Counter, deque
from typing import Any

_log = logging.getLogger("openplan.telemetry")


class TelemetryTracker:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, deque[tuple[str, dict[str, Any]]]] = {}
        self._last_suggestion: dict[str, dict[str, Any]] = {}
        self._suggestion_hits: dict[str, dict[str, dict[str, int]]] = {}
        self._window = 50

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
        if suggestion:
            suggested_tool = suggestion.get("tool")
        else:
            suggested_tool = None
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
            total_followed = 0
            total_ignored = 0
            for tool_rec in self._suggestion_hits.get(sid, {}).values():
                total_followed += tool_rec["followed"]
                total_ignored += tool_rec["ignored"]
            total_suggestions = total_followed + total_ignored
            result: dict[str, Any] = {
                "calls": len(calls),
                "tool_counts": dict(tool_counts.most_common(5)),
            }
            if stuck:
                result["stuck"] = True
                result["stuck_detail"] = "observe called 3+ times without act"
            if total_suggestions > 0:
                result["suggestion_conversion"] = {
                    "followed": total_followed,
                    "ignored": total_ignored,
                    "rate": round(total_followed / total_suggestions, 2),
                }
            return result

    def get_suggestion_conversion(self, session_id: str) -> dict[str, Any] | None:
        sid = session_id or "_default"
        if sid not in self._suggestion_hits:
            return None
        with self._lock:
            total_followed = 0
            total_ignored = 0
            for tool_rec in self._suggestion_hits[sid].values():
                total_followed += tool_rec["followed"]
                total_ignored += tool_rec["ignored"]
            total_suggestions = total_followed + total_ignored
            if total_suggestions == 0:
                return None
            return {"followed": total_followed, "ignored": total_ignored, "rate": round(total_followed / total_suggestions, 2)}

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
        if not hasattr(self, "_conn") or not self._conn:
            return
        with self._lock:
            for sid, hits in self._suggestion_hits.items():
                total_followed = sum(r["followed"] for r in hits.values())
                total_ignored = sum(r["ignored"] for r in hits.values())
                total = total_followed + total_ignored
                if total == 0:
                    continue
                for tool, rec in hits.items():
                    digest = hashlib.sha256(f"telemetry:{sid}:{tool}".encode()).hexdigest()
                    eid = int(digest[:12], 16) % 1000000
                    ikey = digest[:32]
                    self._conn.execute(
                        "INSERT OR IGNORE INTO events (id, project, node_id, event_type, payload, version, idempotency_key, session_id, created_at) "
                        "VALUES (?, '__telemetry__', '__telemetry__', 'telemetry', ?, 1, ?, '', ?)",
                        (f"E-{eid:06d}", json.dumps({"session": sid, "tool": tool, "followed": rec["followed"], "ignored": rec["ignored"]}), ikey, ""),
                    )
            if self._conn:
                self._conn.commit()

    def reload_from_events(self) -> None:
        if not hasattr(self, "_conn") or not self._conn:
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
                    self._suggestion_hits[sid][payload["tool"]] = {"followed": payload["followed"], "ignored": payload["ignored"]}
            except Exception:
                _log.exception("Failed to reload telemetry from events")


_telemetry = TelemetryTracker()


def get_telemetry() -> TelemetryTracker:
    return _telemetry
