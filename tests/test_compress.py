from __future__ import annotations

import json
import sqlite3

import pytest

from openplan.core.activation import reset_cache
from openplan.core.state import act, branch, generate_id
from openplan.core.export import compress, project_list
from openplan.db.schema import init_db


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    reset_cache()


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    return c


@pytest.fixture
def config() -> dict:
    return {
        "stale_days": 2,
        "activation_weights": {"in_degree": 0.4, "frontier": 0.3, "recency": 0.2, "boost": 0.1},
        "activation_threshold": 0.5,
    }


def _make_node(conn: sqlite3.Connection, project: str = "test", label: str = "") -> str:
    sid = generate_id(project, conn)
    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES (?, ?, ?)", (sid, label, project)
    )
    return sid


def test_project_list_empty(conn: sqlite3.Connection) -> None:
    result = project_list(conn)
    assert result["count"] == 0
    assert result["projects"] == []


def test_project_list_single(conn: sqlite3.Connection) -> None:
    _make_node(conn, "alpha", "Alpha root")
    result = project_list(conn)
    assert result["count"] == 1
    assert result["projects"] == ["alpha"]
    assert "roots" in result


def test_project_list_multi(conn: sqlite3.Connection) -> None:
    a1 = _make_node(conn, "alpha", "Alpha root")
    a2 = _make_node(conn, "alpha", "Alpha child")
    b1 = _make_node(conn, "beta", "Beta root")
    result = project_list(conn)
    assert result["count"] == 2
    assert "alpha" in result["projects"]
    assert "beta" in result["projects"]
    roots = result["roots"]
    assert roots["alpha"]["root_id"] == a1
    assert roots["beta"]["root_id"] == b1


def test_compress_archive_events(conn: sqlite3.Connection, config: dict) -> None:
    sid = _make_node(conn, "test", "root")
    opts = [{"label": "Old work", "action": "implement", "prob": 0.8, "expected_cost": {"tokens": 1000, "risk": 0.1}}]
    branch(sid, opts, conn, config)

    old_event = conn.execute("SELECT * FROM events WHERE event_type = 'branched'").fetchone()
    assert old_event is not None

    conn.execute(
        "UPDATE events SET created_at = '2020-01-01T00:00:00.000Z' WHERE id = ?",
        (old_event["id"],),
    )

    result = compress("test", conn, config, older_than_days=1)
    assert result["ok"] is True
    assert result["archived_events"] >= 1

    remaining = conn.execute("SELECT COUNT(*) AS cnt FROM events WHERE event_type = 'branched'").fetchone()["cnt"]
    assert remaining == 0

    archived = conn.execute("SELECT COUNT(*) AS cnt FROM events_archive WHERE event_type = 'branched'").fetchone()["cnt"]
    assert archived >= 1


def test_compress_no_old_events(conn: sqlite3.Connection, config: dict) -> None:
    sid = _make_node(conn, "test", "root")
    opts = [{"label": "Fresh work", "action": "implement", "prob": 0.8, "expected_cost": {"tokens": 1000, "risk": 0.1}}]
    branch(sid, opts, conn, config)

    result = compress("test", conn, config, older_than_days=36500)
    assert result["ok"] is True
    assert result["archived_events"] == 0
    assert result["merged_orphans"] == 0


def test_compress_empty_project(conn: sqlite3.Connection, config: dict) -> None:
    result = compress("nonexistent", conn, config, older_than_days=1)
    assert result["ok"] is True
    assert result["archived_events"] == 0
    assert result["deleted_events"] == 0
    assert result["merged_orphans"] == 0


def test_compress_orphan_merge(conn: sqlite3.Connection, config: dict) -> None:
    parent = _make_node(conn, "test", "active parent")
    orphan1 = _make_node(conn, "test", "orphan one")
    orphan2 = _make_node(conn, "test", "orphan two")
    for oid in (orphan1, orphan2):
        conn.execute(
            "UPDATE nodes SET props = ? WHERE id = ?",
            (json.dumps({"boost": False}), oid),
        )

    result = compress("test", conn, config, older_than_days=36500, merge_orphans=True)
    assert result["ok"] is True
    assert result["merged_orphans"] >= 2

    for oid in (orphan1, orphan2):
        edge = conn.execute(
            "SELECT * FROM edges WHERE source_id = ? AND target_id = ? AND action = 'merged'",
            (parent, oid),
        ).fetchone()
        assert edge is not None, f"Expected merged edge for {oid}"
