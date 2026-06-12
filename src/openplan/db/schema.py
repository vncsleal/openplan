from __future__ import annotations

import sqlite3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nodes (
    id         TEXT PRIMARY KEY,
    label      TEXT NOT NULL DEFAULT '',
    activation REAL NOT NULL DEFAULT 0.0,
    frontier   INTEGER NOT NULL DEFAULT 0,
    project    TEXT NOT NULL,
    props      TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS edges (
    source_id    TEXT NOT NULL REFERENCES nodes(id),
    target_id    TEXT NOT NULL REFERENCES nodes(id),
    action       TEXT NOT NULL,
    cost_tokens  REAL NOT NULL DEFAULT 10000.0,
    cost_risk    REAL NOT NULL DEFAULT 0.1,
    prob         REAL NOT NULL DEFAULT 0.8,
    weight_history TEXT NOT NULL DEFAULT '[]',
    conditions    TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (source_id, target_id, action)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,
    project         TEXT NOT NULL,
    node_id         TEXT NOT NULL REFERENCES nodes(id),
    event_type      TEXT NOT NULL,
    payload         TEXT NOT NULL DEFAULT '{}',
    version         INTEGER NOT NULL DEFAULT 1,
    idempotency_key TEXT,
    session_id      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_events_idempotency ON events(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_nodes_project ON nodes(project);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id, action);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_events_node ON events(node_id, version);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

CREATE TABLE IF NOT EXISTS events_archive (
    id              TEXT PRIMARY KEY,
    project         TEXT NOT NULL,
    node_id         TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    payload         TEXT NOT NULL DEFAULT '{}',
    version         INTEGER NOT NULL DEFAULT 1,
    idempotency_key TEXT,
    session_id      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(label, project);

CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
    INSERT OR REPLACE INTO nodes_fts(rowid, label, project) VALUES (new.rowid, new.label, new.project);
END;

CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE OF label ON nodes BEGIN
    UPDATE nodes_fts SET label = new.label WHERE rowid = new.rowid;
END;

CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT NOT NULL DEFAULT '',
    project        TEXT NOT NULL,
    cursor_state_id TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (session_id, project)
);

CREATE TABLE IF NOT EXISTS cross_project_insights (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_project TEXT NOT NULL,
    source_state   TEXT NOT NULL,
    target_project TEXT NOT NULL,
    target_state   TEXT NOT NULL,
    insight_text   TEXT NOT NULL,
    similarity     REAL NOT NULL DEFAULT 0.0,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(source_project, source_state, target_project, target_state, insight_text)
);
CREATE INDEX IF NOT EXISTS idx_cpi_target ON cross_project_insights(target_project, target_state);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    for col in ("idempotency_key TEXT", "session_id TEXT NOT NULL DEFAULT ''"):
        try:
            conn.execute(f"ALTER TABLE events_archive ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("DROP TRIGGER IF EXISTS nodes_ai")
        conn.execute("DROP TRIGGER IF EXISTS nodes_au")
        conn.executescript("""
            CREATE TRIGGER nodes_ai AFTER INSERT ON nodes BEGIN
                INSERT OR REPLACE INTO nodes_fts(rowid, label, project) VALUES (new.rowid, new.label, new.project);
            END;
            CREATE TRIGGER nodes_au AFTER UPDATE OF label ON nodes BEGIN
                UPDATE nodes_fts SET label = new.label WHERE rowid = new.rowid;
            END;
        """)
        conn.execute("DELETE FROM nodes_fts WHERE rowid NOT IN (SELECT rowid FROM nodes)")
    except Exception:
        pass
    for col in ("status TEXT NOT NULL DEFAULT 'pending'",):
        try:
            conn.execute(f"ALTER TABLE nodes ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    for col in ("goal TEXT NOT NULL DEFAULT ''", "project_type TEXT NOT NULL DEFAULT ''", "terminal INTEGER NOT NULL DEFAULT 0"):
        try:
            conn.execute(f"ALTER TABLE nodes ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    for col in ("parent_id TEXT REFERENCES nodes(id)",):
        try:
            conn.execute(f"ALTER TABLE nodes ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cost_baselines (
            project_type TEXT NOT NULL DEFAULT '',
            project      TEXT,
            action       TEXT NOT NULL,
            cost_tokens  REAL NOT NULL DEFAULT 10000.0,
            cost_risk    REAL NOT NULL DEFAULT 0.1,
            sample_count INTEGER NOT NULL DEFAULT 1,
            updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            PRIMARY KEY (project_type, action, project)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS self_diagnostics (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            metric      TEXT NOT NULL,
            value       REAL NOT NULL,
            threshold   REAL NOT NULL DEFAULT 0.0,
            severity    TEXT NOT NULL DEFAULT 'info',
            detail      TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
    """)
    _migrate_v0_3_0(conn)
    try_init_vec0(conn)


def _migrate_v0_3_0(conn: sqlite3.Connection) -> None:
    has_goal = any(
        r["name"] == "goal"
        for r in conn.execute("PRAGMA table_info(nodes)").fetchall()
    )
    if not has_goal:
        return
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) "
        "SELECT 'goal:' || project, json_object('text', goal, 'target_state_id', NULL) "
        "FROM nodes WHERE goal != '' AND goal IS NOT NULL"
    )
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript("""
        CREATE TABLE nodes_v2 (
            id         TEXT PRIMARY KEY,
            label      TEXT NOT NULL DEFAULT '',
            activation REAL NOT NULL DEFAULT 0.0,
            frontier   INTEGER NOT NULL DEFAULT 0,
            project    TEXT NOT NULL,
            props      TEXT NOT NULL DEFAULT '{}',
            parent_id  TEXT REFERENCES nodes_v2(id),
            status     TEXT NOT NULL DEFAULT 'pending',
            project_type TEXT NOT NULL DEFAULT '',
            terminal   INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        INSERT INTO nodes_v2 SELECT
            id, label, activation, frontier, project, props,
            parent_id, status, project_type, terminal, created_at, updated_at
        FROM nodes;
        DROP TABLE nodes;
        ALTER TABLE nodes_v2 RENAME TO nodes;
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_project ON nodes(project)")
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
            INSERT OR REPLACE INTO nodes_fts(rowid, label, project)
            VALUES (new.rowid, new.label, new.project);
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE OF label ON nodes BEGIN
            UPDATE nodes_fts SET label = new.label WHERE rowid = new.rowid;
        END
    """)
    conn.executescript("""
        DELETE FROM nodes_fts;
        INSERT INTO nodes_fts(rowid, label, project) SELECT rowid, label, project FROM nodes;
    """)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("ALTER TABLE cost_baselines ADD COLUMN project TEXT")
    except sqlite3.OperationalError:
        pass


def try_init_vec0(conn: sqlite3.Connection) -> bool:
    """Try to initialise sqlite-vec ANN index. Idempotent."""
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS vec_embeddings "
            "USING vec0(embedding float[384] distance_metric=cosine)"
        )
        return True
    except Exception:
        return False
