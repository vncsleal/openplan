from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from typing import Any

_log = logging.getLogger("openplan.telemetry_client")


class TelemetryClient:
    def __init__(self, endpoint: str = "", enabled: bool = False) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._enabled = enabled and bool(endpoint)
        self._lock = threading.Lock()
        self._buffer: list[dict[str, Any]] = []
        self._flush_interval = 30.0
        self._last_flush = 0.0
        self._session_id = os.environ.get("OPENCODE_SESSION_ID", "")

    def configure(self, endpoint: str, enabled: bool) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._enabled = enabled and bool(endpoint)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def record(
        self,
        project_type: str,
        action: str,
        expected_cost: float | None,
        actual_cost: float,
        outcome: str,
    ) -> None:
        if not self._enabled:
            return
        event = {
            "project_type": project_type or "",
            "action": action,
            "expected_cost": expected_cost,
            "actual_cost": actual_cost,
            "outcome": outcome,
            "session_id": self._session_id,
            "timestamp": time.time(),
        }
        with self._lock:
            self._buffer.append(event)
            now = time.time()
            if now - self._last_flush >= self._flush_interval:
                self._last_flush = now
                buffer = list(self._buffer)
                self._buffer.clear()
                threading.Thread(target=self._send, args=(buffer,), daemon=True).start()

    def flush(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            buffer = list(self._buffer)
            self._buffer.clear()
        if buffer:
            self._send(buffer)

    def _send(self, events: list[dict[str, Any]]) -> None:
        if not events or not self._endpoint:
            return
        try:
            payload = json.dumps({"events": events}).encode("utf-8")
            req = urllib.request.Request(
                f"{self._endpoint}/telemetry",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            _log.debug("Telemetry send failed (non-blocking)")

    def fetch_calibration(self, conn: sqlite3.Connection) -> int:
        if not self._endpoint:
            return 0
        try:
            req = urllib.request.Request(f"{self._endpoint}/calibration")
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            _log.debug("Calibration fetch failed (non-blocking)")
            return 0

        baselines = data.get("baselines", []) if isinstance(data, dict) else data
        count = 0
        now = time.strftime("%Y-%m-%dT%H:%M:%S.%fZ", time.gmtime())
        for bl in baselines:
            project_type = bl.get("project_type", "")
            action = bl.get("action", "")
            cost_tokens = bl.get("cost_tokens")
            sample_count = bl.get("sample_count", 1)
            if not action or not cost_tokens:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO cost_baselines "
                "(project, project_type, action, cost_tokens, cost_risk, sample_count, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (None, project_type, action, cost_tokens, 0.1, sample_count, now),
            )
            count += 1
        if count:
            conn.commit()
            _log.info("Imported %d global calibration baselines", count)
        return count


_telemetry_client = TelemetryClient()


def get_telemetry_client() -> TelemetryClient:
    return _telemetry_client
