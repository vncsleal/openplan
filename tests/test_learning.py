from __future__ import annotations

import json
import sqlite3

import pytest

from openplan.core.learning import tune
from openplan.db.schema import init_db


@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    yield conn
    conn.close()


def _make_edge(conn: sqlite3.Connection, source: str, target: str, action: str = "implement", wh: str | None = None) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob, weight_history) VALUES (?, ?, ?, ?, ?, 0.8, ?)",
        (source, target, action, 8000, 0.1, wh or "[]"),
    )


def _make_node(conn: sqlite3.Connection, project: str = "test") -> str:
    row = conn.execute("SELECT MAX(CAST(SUBSTR(id, 3) AS INTEGER)) AS max_id FROM nodes").fetchone()
    next_num = (row["max_id"] or 0) + 1
    sid = f"S-{next_num:06d}"
    conn.execute("INSERT INTO nodes (id, label, project) VALUES (?, ?, ?)", (sid, "", project))
    return sid


def test_tune_empty(conn: sqlite3.Connection) -> None:
    result = tune(conn, {})
    assert result["ok"] is True
    assert result["actions_tuned"] == 0
    assert result["recommendations"] == {}


def test_tune_single_action(conn: sqlite3.Connection) -> None:
    a = _make_node(conn)
    b = _make_node(conn)
    wh = json.dumps([{"actual_cost": {"tokens": 5000}, "expected_cost": {"tokens": 8000}, "outcome": "success"}])
    _make_edge(conn, a, b, "implement", wh)

    result = tune(conn, {})
    assert result["actions_tuned"] == 1
    assert "implement" in result["recommendations"]
    impl = result["recommendations"]["implement"]
    assert impl["count"] == 1
    assert impl["avg_cost"] == 8000.0
    assert impl["success_rate"] == 1.0


def test_tune_multi_action(conn: sqlite3.Connection) -> None:
    a = _make_node(conn)
    b = _make_node(conn)
    c = _make_node(conn)
    wh_a = json.dumps([{"actual_cost": {"tokens": 5000}, "outcome": "success"}])
    wh_b = json.dumps([{"actual_cost": {"tokens": 12000}, "outcome": "failure"}])
    _make_edge(conn, a, b, "implement", wh_a)
    _make_edge(conn, a, c, "research", wh_b)

    result = tune(conn, {})
    assert result["actions_tuned"] == 2
    assert result["recommendations"]["implement"]["count"] == 1
    assert result["recommendations"]["research"]["count"] == 1


def test_tune_ignores_auto(conn: sqlite3.Connection) -> None:
    a = _make_node(conn)
    b = _make_node(conn)
    wh = json.dumps([{"actual_cost": {"tokens": 10000}, "expected_cost": {"tokens": 10000}, "auto": True}])
    _make_edge(conn, a, b, "implement", wh)

    result = tune(conn, {})
    assert result["actions_tuned"] == 1
    assert result["recommendations"]["implement"]["success_rate"] == 0.0


def test_tune_updates_meta(conn: sqlite3.Connection) -> None:
    a = _make_node(conn)
    b = _make_node(conn)
    _make_edge(conn, a, b, "design", "[]")
    tune(conn, {})
    row = conn.execute("SELECT value FROM meta WHERE key = 'tuning:design'").fetchone()
    assert row is not None
    val = json.loads(row["value"])
    assert val["avg_cost"] == 8000.0
