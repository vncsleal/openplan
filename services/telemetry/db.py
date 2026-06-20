from __future__ import annotations

import json
import math
import os
import time
from typing import Any

import httpx

# ─── Turso HTTP adapter ────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _TursoHTTP:
    def __init__(self, url: str, token: str) -> None:
        self._url = url.replace("libsql://", "https://")
        self._auth = f"Bearer {token}"
        self._client = httpx.Client()

    def _exec(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        stmt: dict[str, Any] = {"sql": sql}
        if params:
            stmt["args"] = [{"type": "text", "value": str(p)} for p in params]
        resp = self._client.post(
            f"{self._url}/v2/pipeline",
            json={"requests": [{"type": "execute", "stmt": stmt}]},
            headers={"Authorization": self._auth},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = []
        for result in data.get("results", []):
            inner = result.get("response", {}).get("result", {})
            cols = [c["name"] for c in inner.get("cols", [])]
            for row_data in inner.get("rows", []):
                rows.append(dict(zip(cols, [r.get("value") for r in row_data])))
        return rows

    def execute(self, sql: str, params: tuple = ()) -> _Result:
        return _Result(self._exec(sql, params))

    def executescript(self, sql: str) -> None:
        for stmt in sql.strip().split(";"):
            s = stmt.strip()
            if s:
                self._exec(s)

    def commit(self) -> None:
        pass

    def close(self) -> None:
        self._client.close()


# ─── Connection factory ────────────────────────────────────────────────────

TURSO_URL = os.environ.get("OPENPLAN_DB_URL", "")
if TURSO_URL:
    _create_conn = lambda: _TursoHTTP(
        TURSO_URL, os.environ.get("OPENPLAN_DB_TOKEN", "")
    )
else:
    import sqlite3

    _db_path = os.environ.get("OPENPLAN_DB_PATH") or os.path.join(
        os.environ.get(
            "OPENPLAN_DATA_DIR", os.path.expanduser("~/.local/share/openplan")
        ),
        "telemetry.db",
    )
    _create_conn = lambda: sqlite3.connect(_db_path)


def get_conn():
    conn = _create_conn()
    if not TURSO_URL and hasattr(conn, "row_factory"):
        conn.row_factory = sqlite3.Row
    return conn


# ─── Schema ─────────────────────────────────────────────────────────────────


def init_db(conn: Any) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS calibration_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key       TEXT NOT NULL DEFAULT '',
            project_type  TEXT NOT NULL DEFAULT '',
            action        TEXT NOT NULL,
            phase_label_tokens TEXT NOT NULL DEFAULT '',
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
        CREATE INDEX IF NOT EXISTS idx_oauth_access_token ON oauth_sessions(access_token);

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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
    """)
    # Migration: add phase_label_tokens column if missing
    try:
        conn.execute(
            "ALTER TABLE calibration_events ADD COLUMN phase_label_tokens TEXT NOT NULL DEFAULT ''"
        )
    except Exception:
        pass
    _ensure_weight_column(conn)
    conn.commit()


# ─── Data operations ───────────────────────────────────────────────────────


def insert_event(
    conn: Any, api_key: str, event: dict[str, Any], weight: float = 1.0
) -> None:
    conn.execute(
        "INSERT INTO calibration_events (api_key, project_type, action, phase_label_tokens, expected_cost, actual_cost, outcome, session_id, created_at, weight) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            api_key,
            event.get("project_type", ""),
            event.get("action", ""),
            event.get("phase_label_tokens", ""),
            event.get("expected_cost"),
            event.get("actual_cost", 0),
            event.get("outcome", "success"),
            event.get("session_id", ""),
            event.get("timestamp") or time.time(),
            weight,
        ),
    )


def _ensure_weight_column(conn: Any) -> None:
    """Ensure the weight column exists on calibration_events (idempotent)."""
    try:
        info = conn.execute("PRAGMA table_info(calibration_events)").fetchall()
        cols = [r["name"] for r in info]
        if "weight" not in cols:
            conn.execute(
                "ALTER TABLE calibration_events ADD COLUMN weight REAL NOT NULL DEFAULT 1.0"
            )
            conn.commit()
    except Exception:
        pass


def _get_records(conn: Any) -> list[dict[str, Any]]:
    """Fetch calibration records, normalizing sqlite3.Row to dict and converting types."""
    cutoff = time.time() - 30 * 86400
    rows = conn.execute(
        """
        SELECT project_type, action, phase_label_tokens, actual_cost, api_key
        FROM calibration_events
        WHERE actual_cost IS NOT NULL AND actual_cost > 0
          AND created_at >= ?
        ORDER BY project_type, action, phase_label_tokens
    """,
        (cutoff,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r) if not isinstance(r, dict) else dict(r)
        d["actual_cost"] = float(d["actual_cost"])
        result.append(d)
    return result


def get_calibration(
    conn: Any, min_samples: int = 3, min_contributors: int = 2
) -> list[dict[str, Any]]:
    rows = _get_records(conn)

    if not rows:
        return []

    # Try reading weights; fall back to 1.0 if column doesn't exist
    weights_by_key: dict[str, float] = {}
    try:
        weight_rows = conn.execute(
            "SELECT api_key, weight FROM calibration_events WHERE created_at >= ?",
            (time.time() - 30 * 86400,),
        ).fetchall()
        for wr in weight_rows:
            d = dict(wr) if not isinstance(wr, dict) else dict(wr)
            weights_by_key[d["api_key"]] = float(d["weight"])
    except Exception:
        weights_by_key = {}

    def _weight(api_key: str) -> float:
        return weights_by_key.get(api_key, 1.0)

    # Group by (project_type, action, match_level)
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for r in rows:
        tokens = (r.get("phase_label_tokens") or "").split()
        token_set = set(tokens)
        if len(token_set) >= 2:
            match_level = "exact"
        elif len(token_set) >= 1:
            match_level = "label_keyword"
        else:
            match_level = "action"
        key = (r["project_type"], r["action"], match_level)
        if key not in groups:
            groups[key] = []
        groups[key].append(r)

    # Compute global priors: median per action across all project_types
    action_values: dict[str, list[float]] = {}
    for r in rows:
        action_values.setdefault(r["action"], []).append(r["actual_cost"])

    action_priors: dict[str, float] = {}
    for action, vals in action_values.items():
        sorted_vals = sorted(vals)
        action_priors[action] = _percentile(sorted_vals, 0.5)

    KAPPA = 10.0
    MAD_THRESHOLD = 20

    results: list[dict[str, Any]] = []
    for (pt, action, match_level), recs in groups.items():
        contributors = set(r["api_key"] for r in recs)
        if len(recs) < min_samples or len(contributors) < min_contributors:
            continue

        all_values = [r["actual_cost"] for r in recs]
        all_weights = [_weight(r["api_key"]) for r in recs]

        # Layer 2: MAD filter — only for buckets with >= 20 samples
        if len(all_values) >= MAD_THRESHOLD:
            keep = _mad_filter_indices(all_values)
            clean_values = [all_values[i] for i in keep]
            clean_weights = [all_weights[i] for i in keep]
        else:
            clean_values = all_values
            clean_weights = all_weights

        n = len(clean_values)
        if n < min_samples:
            continue

        # Layer 3: Bayesian shrinkage — for buckets with < 20 clean samples
        prior = action_priors.get(action)
        if n < MAD_THRESHOLD and prior is not None:
            bucket_median = _weighted_percentile(
                list(zip(clean_values, clean_weights)), 0.5
            )
            effective_n = sum(clean_weights)
            estimate = (effective_n * bucket_median + KAPPA * prior) / (
                effective_n + KAPPA
            )
        else:
            estimate = _weighted_percentile(list(zip(clean_values, clean_weights)), 0.5)

        pairs = list(zip(clean_values, clean_weights))
        results.append(
            {
                "project_type": pt,
                "action": action,
                "match_level": match_level,
                "cost_tokens": round(estimate, 2),
                "sample_count": n,
                "p50": round(_weighted_percentile(pairs, 0.5), 2),
                "p25": round(_weighted_percentile(pairs, 0.25), 2),
                "p75": round(_weighted_percentile(pairs, 0.75), 2),
            }
        )
    return results


# ─── Statistical helpers ──────────────────────────────────────────────────


def _percentile(sorted_vals: list[float], p: float) -> float:
    k = (len(sorted_vals) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def _mad_filter_indices(values: list[float]) -> list[int]:
    """Return indices of values where |z_robust| <= 3 using MAD-based z-score."""
    n = len(values)
    sorted_vals = sorted(values)
    median = _percentile(sorted_vals, 0.5)
    abs_devs = [abs(v - median) for v in values]
    mad = _percentile(sorted(abs_devs), 0.5)
    if mad == 0:
        return list(range(n))
    mad_scaled = 1.4826 * mad
    return [i for i, v in enumerate(values) if abs(v - median) / mad_scaled <= 3]


def _weighted_percentile(
    value_weight_pairs: list[tuple[float, float]], p: float
) -> float:
    """Compute percentile using cumulative weight instead of count."""
    if not value_weight_pairs:
        return 0.0
    pairs = sorted(value_weight_pairs, key=lambda x: x[0])
    n = len(pairs)
    weights = [w for _, w in pairs]
    total_weight = sum(weights)
    if total_weight == 0:
        return 0.0
    cum_weights: list[float] = []
    running = 0.0
    for w in weights:
        running += w
        cum_weights.append(running / total_weight)
    target = p
    for i, cw in enumerate(cum_weights):
        if cw >= target:
            if i == 0:
                return pairs[i][0]
            lower = cum_weights[i - 1]
            upper = cw
            if upper == lower:
                return pairs[i][0]
            frac = (target - lower) / (upper - lower)
            return pairs[i - 1][0] + (pairs[i][0] - pairs[i - 1][0]) * frac
    return pairs[-1][0]


def get_identity_volume_ratio(conn: Any, api_key: str) -> float:
    """Fraction of calibration events in the last 24h belonging to this key."""
    cutoff = time.time() - 86400
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN api_key = ? THEN 1 ELSE 0 END) AS key_count,
            COUNT(*) AS total_count
        FROM calibration_events
        WHERE created_at >= ?
        """,
        (api_key, cutoff),
    ).fetchone()
    if not row or not row["total_count"]:
        return 0.0
    return float(row["key_count"]) / float(row["total_count"])


def get_rate_limit(conn: Any, api_key: str, window_seconds: int = 60) -> int:
    now = time.time()
    window_start = math.floor(now / window_seconds) * window_seconds
    row = conn.execute(
        "SELECT count FROM rate_limits WHERE api_key = ? AND window_start = ?",
        (api_key, window_start),
    ).fetchone()
    return int(row["count"]) if row else 0


def increment_rate_limit(
    conn: Any, api_key: str, window_seconds: int = 60, count: int = 1
) -> None:
    now = time.time()
    window_start = math.floor(now / window_seconds) * window_seconds
    conn.execute(
        "INSERT OR REPLACE INTO rate_limits (api_key, window_start, count) "
        "VALUES (?, ?, COALESCE((SELECT count + ? FROM rate_limits WHERE api_key = ? AND window_start = ?), ?))",
        (api_key, window_start, count, api_key, window_start, count),
    )
    # Lazy cleanup: purge entries older than max window + safety margin
    conn.execute(
        "DELETE FROM rate_limits WHERE window_start < ?",
        (math.floor((now - 90000) / 60) * 60,),
    )


def get_key_usage(conn: Any, api_key: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT COUNT(*) AS cnt, MIN(created_at) AS first_seen, MAX(created_at) AS last_seen FROM calibration_events WHERE api_key = ?",
        (api_key,),
    ).fetchone()
    if not row:
        return {"event_count": 0, "first_seen": None, "last_seen": None}
    return {
        "event_count": int(row["cnt"]),
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
    }
