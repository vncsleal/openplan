#!/usr/bin/env python3
"""
OpenPlan Telemetry Server — stdlib-only, single-file, zero dependencies.

Usage:
    python scripts/telemetry_server.py [--port PORT] [--db DB_PATH]

Routes:
    POST /telemetry  — accepts {"events": [{project_type, action, expected_cost, actual_cost, outcome}, ...]}
    GET  /calibration — returns {"baselines": [{project_type, action, cost_tokens, sample_count}, ...]}
    GET  /health     — returns {"ok": true, "events_count": N}
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

DB_PATH = os.environ.get("TELEMETRY_DB", "telemetry.db")


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calibration_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_type TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL,
            expected_cost REAL,
            actual_cost REAL NOT NULL,
            outcome TEXT NOT NULL DEFAULT 'success',
            session_id TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_calibration_lookup
        ON calibration_events (project_type, action, created_at)
    """)
    conn.commit()


def record_event(conn: sqlite3.Connection, event: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO calibration_events (project_type, action, expected_cost, actual_cost, outcome, session_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            event.get("project_type", ""),
            event.get("action", ""),
            event.get("expected_cost"),
            event.get("actual_cost", 0),
            event.get("outcome", "success"),
            event.get("session_id", ""),
            event.get("timestamp", time.time()),
        ),
    )


def get_calibration(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("""
        SELECT
            project_type,
            action,
            COUNT(*) AS sample_count,
            AVG(actual_cost) AS cost_tokens
        FROM calibration_events
        WHERE actual_cost IS NOT NULL AND actual_cost > 0
        GROUP BY project_type, action
        HAVING sample_count >= 3
        ORDER BY sample_count DESC
    """).fetchall()
    return [
        {"project_type": r["project_type"], "action": r["action"],
         "cost_tokens": round(r["cost_tokens"], 2), "sample_count": r["sample_count"]}
        for r in rows
    ]


def get_health(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT COUNT(*) AS cnt FROM calibration_events").fetchone()
    return {"ok": True, "events_count": row["cnt"] if row else 0}


class TelemetryHandler(BaseHTTPRequestHandler):
    conn: sqlite3.Connection = None  # type: ignore[assignment]

    def _respond(self, code: int, data: dict[str, Any]) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/calibration":
            baselines = get_calibration(self.conn)
            self._respond(200, {"baselines": baselines})
        elif parsed.path == "/health":
            health = get_health(self.conn)
            self._respond(200, health)
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/telemetry":
            self._respond(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            self._respond(400, {"error": "empty body"})
            return
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._respond(400, {"error": "invalid JSON"})
            return
        events = body if isinstance(body, list) else body.get("events", [body])
        count = 0
        for ev in events:
            if ev.get("actual_cost") is not None:
                record_event(self.conn, ev)
                count += 1
        self.conn.commit()
        self._respond(200, {"ok": True, "accepted": count})

    def log_message(self, fmt: str, *args: Any) -> None:
        timestamp = time.strftime("%H:%M:%S")
        sys.stderr.write(f"[{timestamp}] {args[0]} {args[1]} {args[2]}\n")


def main() -> None:
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else int(os.environ.get("PORT", 8888))
    db = sys.argv[sys.argv.index("--db") + 1] if "--db" in sys.argv else DB_PATH

    conn = sqlite3.connect(db, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)

    TelemetryHandler.conn = conn
    server = HTTPServer(("0.0.0.0", port), TelemetryHandler)

    print(f"telemetry endpoint listening on http://0.0.0.0:{port}")
    print(f"  POST /telemetry  — submit calibration events")
    print(f"  GET  /calibration — get aggregated baselines")
    print(f"  GET  /health       — server status")
    print(f"  DB: {db}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        server.server_close()
        conn.close()


if __name__ == "__main__":
    main()
