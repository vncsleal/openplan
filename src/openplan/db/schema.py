import sqlite3
import json
import os

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS routes (
    id            TEXT PRIMARY KEY,
    project       TEXT NOT NULL,
    goal          TEXT NOT NULL DEFAULT '',
    context       TEXT NOT NULL DEFAULT '',
    total_expected REAL NOT NULL DEFAULT 0.0,
    total_actual  REAL,
    status        TEXT NOT NULL DEFAULT 'active',
    archived      INTEGER NOT NULL DEFAULT 0,
    abandon_reason TEXT NOT NULL DEFAULT '',
    goal_tokens   TEXT NOT NULL DEFAULT '',
    context_tokens TEXT NOT NULL DEFAULT '',
    completed_at  TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS route_phases (
    id            TEXT PRIMARY KEY,
    route_id      TEXT NOT NULL REFERENCES routes(id),
    label         TEXT NOT NULL,
    action        TEXT NOT NULL DEFAULT 'implement',
    expected_cost REAL NOT NULL DEFAULT 0.0,
    actual_cost   REAL,
    outcome       TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    sequence      INTEGER NOT NULL,
    label_tokens  TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS calibration_events (
    id              TEXT PRIMARY KEY,
    action          TEXT NOT NULL,
    phase_label_tokens TEXT NOT NULL DEFAULT '',
    expected_cost   REAL NOT NULL DEFAULT 0.0,
    actual_cost     REAL NOT NULL DEFAULT 0.0,
    outcome         TEXT NOT NULL DEFAULT 'success',
    project         TEXT NOT NULL DEFAULT '',
    session_id      TEXT NOT NULL DEFAULT '',
    api_key         TEXT NOT NULL DEFAULT '',
    synced          INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS cost_baselines (
    match_level       TEXT NOT NULL DEFAULT 'action',
    action            TEXT NOT NULL DEFAULT '',
    phase_label_tokens TEXT NOT NULL DEFAULT '',
    avg_cost          REAL NOT NULL DEFAULT 0.0,
    ci_lo             REAL NOT NULL DEFAULT 0.0,
    ci_hi             REAL NOT NULL DEFAULT 0.0,
    sample_count      INTEGER NOT NULL DEFAULT 0,
    success_rate      REAL NOT NULL DEFAULT 0.0,
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (match_level, action, phase_label_tokens)
);

CREATE TABLE IF NOT EXISTS completed_sequences (
    id              TEXT PRIMARY KEY,
    goal_tokens     TEXT NOT NULL DEFAULT '',
    context_tokens  TEXT NOT NULL DEFAULT '',
    action_sequence TEXT NOT NULL DEFAULT '',
    total_expected  REAL NOT NULL DEFAULT 0.0,
    total_actual    REAL NOT NULL DEFAULT 0.0,
    efficiency      REAL NOT NULL DEFAULT 0.0,
    outcome         TEXT NOT NULL DEFAULT 'success',
    session_id      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_routes_project ON routes(project);
CREATE INDEX IF NOT EXISTS idx_route_phases_route ON route_phases(route_id);
CREATE INDEX IF NOT EXISTS idx_calibration_synced ON calibration_events(synced);
CREATE INDEX IF NOT EXISTS idx_calibration_lookup ON calibration_events(action, phase_label_tokens);
CREATE INDEX IF NOT EXISTS idx_sequences_goals ON completed_sequences(goal_tokens);
"""

BUNDLED_DEFAULTS_SQL = """
INSERT OR IGNORE INTO cost_baselines (match_level, action, phase_label_tokens, avg_cost, ci_lo, ci_hi, sample_count, success_rate) VALUES
    ('action', 'implement', '', 2000.0, 500.0, 5000.0, 100, 0.85),
    ('action', 'design', '', 1500.0, 500.0, 4000.0, 50, 0.80),
    ('action', 'deploy', '', 600.0, 300.0, 1000.0, 80, 0.90),
    ('action', 'test', '', 800.0, 400.0, 1500.0, 60, 0.85);
"""


def tokenize(text: str) -> str:
    tokens = []
    for word in text.lower().split():
        clean = "".join(c for c in word if c.isalnum() or c in "-_.")
        if len(clean) > 2 and clean not in _STOP_WORDS:
            tokens.append(clean)
    return " ".join(tokens)


_STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "using", "build",
    "implement", "create", "setup", "add", "make", "get", "use", "need",
    "new", "all", "any", "can", "has", "its", "not", "but", "are", "was",
}


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    # Check if bundled defaults already seeded
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM cost_baselines"
    ).fetchone()
    if row["cnt"] == 0:
        conn.executescript(BUNDLED_DEFAULTS_SQL)
        conn.commit()
