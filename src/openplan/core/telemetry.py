from __future__ import annotations

import threading
from collections import Counter, deque
from typing import Any


class TelemetryTracker:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, deque[tuple[str, dict[str, Any]]]] = {}
        self._last_suggestion: dict[str, dict[str, Any]] = {}
        self._suggestion_hits: dict[str, dict[str, dict[str, int]]] = {}
        self._window = 50

    def record(self, session_id: str, tool: str, args: dict[str, Any] | None = None) -> None:
        if not session_id:
            return
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = deque(maxlen=self._window)
                self._suggestion_hits[session_id] = {}
            self._sessions[session_id].append((tool, args or {}))
            self._check_suggestion_match(session_id, tool)

    def record_suggestion(self, session_id: str, suggestion: dict[str, Any]) -> None:
        if not session_id:
            return
        with self._lock:
            self._last_suggestion[session_id] = suggestion

    def _check_suggestion_match(self, session_id: str, tool: str) -> None:
        suggestion = self._last_suggestion.get(session_id)
        if suggestion:
            suggested_tool = suggestion.get("tool")
        else:
            suggested_tool = None
        if suggested_tool:
            record = self._suggestion_hits[session_id].setdefault(suggested_tool, {"followed": 0, "ignored": 0})
            if tool == suggested_tool:
                record["followed"] += 1
            else:
                record["ignored"] += 1

    def get_session_stats(self, session_id: str) -> dict[str, Any]:
        if not session_id or session_id not in self._sessions:
            return {"calls": 0}
        with self._lock:
            calls = list(self._sessions[session_id])
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
            for tool_rec in self._suggestion_hits.get(session_id, {}).values():
                total_followed += tool_rec["followed"]
                total_ignored += tool_rec["ignored"]
            total_suggestions = total_followed + total_ignored
            result: dict[str, Any] = {
                "calls": total,
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
        if not session_id or session_id not in self._suggestion_hits:
            return None
        with self._lock:
            total_followed = 0
            total_ignored = 0
            for tool_rec in self._suggestion_hits[session_id].values():
                total_followed += tool_rec["followed"]
                total_ignored += tool_rec["ignored"]
            total = total_followed + total_ignored
            if total == 0:
                return None
            return {"followed": total_followed, "ignored": total_ignored, "rate": round(total_followed / total, 2)}


_telemetry = TelemetryTracker()


def get_telemetry() -> TelemetryTracker:
    return _telemetry
