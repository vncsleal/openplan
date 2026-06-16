from __future__ import annotations

import math
import os
import time
from typing import Any

# Use libsql (Turso) if URL is set, otherwise fall back to local sqlite3
TURSO_URL = os.environ.get("OPENPLAN_DB_URL", "")
if TURSO_URL:
    import libsql
    _create_conn = lambda: libsql.connect(url=TURSO_URL, auth_token=os.environ.get("OPENPLAN_DB_TOKEN", ""))
else:
    import sqlite3
    _create_conn = lambda: sqlite3.connect(os.environ.get("OPENPLAN_DB_PATH", "telemetry.db"))


def get_conn():
    conn = _create_conn()
    if not TURSO_URL and hasattr(conn, "row_factory"):
        conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: Any) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS calibration_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key       TEXT NOT NULL DEFAULT '',
            project_type  TEXT NOT NULL DEFAULT '',
            action        TEXT NOT NULL,
            expected_cost REAL,
            actual_cost   REAL NOT NULL,
            outcome       TEXT NOT NULL DEFAULT 'success',
            session_id    TEXT NOT NULL DEFAULT '',
            created_at    REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cal_lookup ON calibration_events(project_type, action, created_at);
        CREATE INDEX IF NOT EXISTS idx_cal_api_key ON calibration_events(api_key, created_at);
    """)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS api_keys (
            key         TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL DEFAULT '',
            tier        TEXT NOT NULL DEFAULT 'free',
            label       TEXT NOT NULL DEFAULT '',
            is_active   INTEGER NOT NULL DEFAULT 1,
            created_at  REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);

        CREATE TABLE IF NOT EXISTS users (
            id          TEXT PRIMARY KEY,
            github_id   INTEGER UNIQUE,
            login       TEXT NOT NULL DEFAULT '',
            email       TEXT NOT NULL DEFAULT '',
            avatar_url  TEXT NOT NULL DEFAULT '',
            created_at  REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS oauth_sessions (
            device_code     TEXT PRIMARY KEY,
            user_code       TEXT NOT NULL,
            github_token    TEXT NOT NULL DEFAULT '',
            access_token    TEXT NOT NULL DEFAULT '',
            refresh_token   TEXT NOT NULL DEFAULT '',
            state           TEXT NOT NULL DEFAULT 'pending',
            created_at      REAL NOT NULL,
            expires_at      REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_oauth_user_code ON oauth_sessions(user_code);

        CREATE TABLE IF NOT EXISTS subscriptions (
            stripe_subscription_id TEXT PRIMARY KEY,
            user_id                TEXT NOT NULL,
            stripe_customer_id     TEXT NOT NULL,
            tier                   TEXT NOT NULL DEFAULT 'pro',
            status                 TEXT NOT NULL DEFAULT 'active',
            current_period_end     REAL NOT NULL,
            created_at             REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_subs_user ON subscriptions(user_id);
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_limits (
            api_key     TEXT NOT NULL,
            window_start REAL NOT NULL,
            count       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (api_key, window_start)
        )
    """)
    conn.commit()


def insert_event(conn: Any, api_key: str, event: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO calibration_events (api_key, project_type, action, expected_cost, actual_cost, outcome, session_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (api_key, event.get("project_type", ""), event.get("action", ""),
         event.get("expected_cost"), event.get("actual_cost", 0),
         event.get("outcome", "success"), event.get("session_id", ""),
         event.get("timestamp") or time.time()),
    )


def get_calibration(conn: Any, min_samples: int = 3, min_contributors: int = 1) -> list[dict[str, Any]]:
    cutoff = time.time() - 30 * 86400  # 30-day window by default
    rows = conn.execute("""
        SELECT project_type, action, actual_cost, api_key
        FROM calibration_events
        WHERE actual_cost IS NOT NULL AND actual_cost > 0
          AND created_at >= ?
        ORDER BY project_type, action
    """, (cutoff,)).fetchall()

    groups: dict[tuple[str, str], list[float]] = {}
    contributors: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        key = (r["project_type"], r["action"])
        if key not in groups:
            groups[key] = []
            contributors[key] = set()
        groups[key].append(r["actual_cost"])
        contributors[key].add(r["api_key"])

    results: list[dict[str, Any]] = []
    for (pt, action), values in groups.items():
        if len(values) < min_samples or len(contributors[(pt, action)]) < min_contributors:
            continue

        sorted_vals = sorted(values)
        n = len(sorted_vals)

        # Trimmed mean: remove top and bottom 10%
        trim = max(1, int(n * 0.1))
        trimmed = sorted_vals[trim:-trim]
        mean = sum(trimmed) / len(trimmed)

        # Percentiles
        p50 = _percentile(sorted_vals, 0.5)
        p25 = _percentile(sorted_vals, 0.25)
        p75 = _percentile(sorted_vals, 0.75)

        results.append({
            "project_type": pt,
            "action": action,
            "cost_tokens": round(mean, 2),
            "sample_count": n,
            "p50": round(p50, 2),
            "p25": round(p25, 2),
            "p75": round(p75, 2),
        })

    return results


def _percentile(sorted_vals: list[float], p: float) -> float:
    k = (len(sorted_vals) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def get_rate_limit(conn: Any, api_key: str, window_seconds: int = 60) -> int:
    now = time.time()
    window_start = math.floor(now / window_seconds) * window_seconds
    row = conn.execute(
        "SELECT count FROM rate_limits WHERE api_key = ? AND window_start = ?",
        (api_key, window_start),
    ).fetchone()
    return row["count"] if row else 0


def increment_rate_limit(conn: Any, api_key: str, window_seconds: int = 60) -> None:
    now = time.time()
    window_start = math.floor(now / window_seconds) * window_seconds
    conn.execute(
        "INSERT OR REPLACE INTO rate_limits (api_key, window_start, count) "
        "VALUES (?, ?, COALESCE((SELECT count + 1 FROM rate_limits WHERE api_key = ? AND window_start = ?), 1))",
        (api_key, window_start, api_key, window_start),
    )
