from __future__ import annotations

import json
import sqlite3

import pytest

from openplan.core.insight_propagation import propagate
from openplan.db.schema import init_db


@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def config() -> dict:
    return {"insight_similarity_threshold": 0.7}


def _make_node(conn: sqlite3.Connection, project: str, label: str = "") -> str:
    row = conn.execute(
        "SELECT COALESCE(MAX(CAST(SUBSTR(id, 3) AS INTEGER)), 0) + 1 AS next FROM nodes"
    ).fetchone()
    sid = f"S-{row['next']:06d}"
    conn.execute("INSERT INTO nodes (id, label, project) VALUES (?, ?, ?)", (sid, label, project))
    return sid


def _calibrated_event(conn: sqlite3.Connection, project: str, node_id: str, insight: str) -> None:
    row = conn.execute(
        "SELECT COALESCE(MAX(CAST(SUBSTR(id, 3) AS INTEGER)), 0) + 1 AS next FROM events"
    ).fetchone()
    eid = f"E-{row['next']:06d}"
    conn.execute(
        "INSERT INTO events (id, project, node_id, event_type, payload) VALUES (?, ?, ?, 'calibrated', ?)",
        (eid, project, node_id, json.dumps({"insight": insight})),
    )
    conn.commit()


def test_propagate_no_events(conn: sqlite3.Connection, config: dict) -> None:
    assert propagate(conn, config) == 0


def test_propagate_bad_payload(conn: sqlite3.Connection, config: dict) -> None:
    n = _make_node(conn, "proj-a")
    row = conn.execute(
        "SELECT COALESCE(MAX(CAST(SUBSTR(id, 3) AS INTEGER)), 0) + 1 AS next FROM events"
    ).fetchone()
    eid = f"E-{row['next']:06d}"
    conn.execute(
        "INSERT INTO events (id, project, node_id, event_type, payload) VALUES (?, ?, ?, 'calibrated', 'not-json')",
        (eid, "proj-a", n),
    )
    conn.commit()
    assert propagate(conn, config) == 0


def test_propagate_empty_insight(conn: sqlite3.Connection, config: dict) -> None:
    n = _make_node(conn, "proj-a")
    row = conn.execute(
        "SELECT COALESCE(MAX(CAST(SUBSTR(id, 3) AS INTEGER)), 0) + 1 AS next FROM events"
    ).fetchone()
    eid = f"E-{row['next']:06d}"
    conn.execute(
        "INSERT INTO events (id, project, node_id, event_type, payload) VALUES (?, ?, ?, 'calibrated', '{}')",
        (eid, "proj-a", n),
    )
    conn.commit()
    assert propagate(conn, config) == 0


def test_propagate_like_fallback(conn: sqlite3.Connection, config: dict) -> None:
    src = _make_node(conn, "proj-a")
    _make_node(conn, "proj-b", label="critical design flaw")
    _calibrated_event(conn, "proj-a", src, "critical performance bottleneck")
    result = propagate(conn, config)
    assert result > 0
    rows = conn.execute("SELECT * FROM cross_project_insights").fetchall()
    assert len(rows) == result
    assert rows[0]["target_project"] == "proj-b"
    assert rows[0]["source_project"] == "proj-a"


def test_propagate_same_project_skip(conn: sqlite3.Connection, config: dict) -> None:
    n = _make_node(conn, "proj-a", label="common issue")
    _calibrated_event(conn, "proj-a", n, "common issue detected")
    result = propagate(conn, config)
    assert result == 0


def test_propagate_short_insight(conn: sqlite3.Connection, config: dict) -> None:
    src = _make_node(conn, "proj-a")
    _make_node(conn, "proj-b", label="test")
    _calibrated_event(conn, "proj-a", src, "hi")
    result = propagate(conn, config)
    assert result == 0


def test_propagate_multiple_events(conn: sqlite3.Connection, config: dict) -> None:
    src1 = _make_node(conn, "proj-a")
    src2 = _make_node(conn, "proj-a")
    _make_node(conn, "proj-b", label="performance regression")
    _make_node(conn, "proj-c", label="memory leak detected")
    _calibrated_event(conn, "proj-a", src1, "performance regression in module")
    _calibrated_event(conn, "proj-a", src2, "memory leak detected in heap")
    result = propagate(conn, config)
    assert result >= 2
    rows = conn.execute("SELECT * FROM cross_project_insights").fetchall()
    assert len(rows) == result
    projects = {(r["source_project"], r["target_project"]) for r in rows}
    assert ("proj-a", "proj-b") in projects
    assert ("proj-a", "proj-c") in projects


def test_propagate_lower_threshold(conn: sqlite3.Connection) -> None:
    src = _make_node(conn, "proj-a")
    _make_node(conn, "proj-b", label="important bug")
    _calibrated_event(conn, "proj-a", src, "important security vulnerability")
    result = propagate(conn, {"insight_similarity_threshold": 0.3})
    assert result > 0
