from __future__ import annotations

import json
import sqlite3

import pytest

from openplan.core.activation import reset_cache
from openplan.core.state import act, generate_id
from openplan.core.export import export
from openplan.db.schema import init_db


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    reset_cache()


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
    return {
        "stale_days": 2,
        "activation_weights": {"in_degree": 0.4, "frontier": 0.3, "recency": 0.2, "boost": 0.1},
        "activation_threshold": 0.5,
    }


def _make_node(
    conn: sqlite3.Connection, project: str = "test", label: str = ""
) -> str:
    sid = generate_id(project, conn)
    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES (?, ?, ?)", (sid, label, project)
    )
    return sid


def _edge(
    conn: sqlite3.Connection,
    source: str,
    target: str,
    action: str = "transition",
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob) VALUES (?, ?, ?, 10000, 0.1, 0.8)",
        (source, target, action),
    )


def test_export_json(conn: sqlite3.Connection, config: dict) -> None:
    a = _make_node(conn)
    b = _make_node(conn)
    _edge(conn, a, b)

    data = export("test", conn)

    assert data["project"] == "test"
    assert data["version"] == "0.1.0"
    assert len(data["nodes"]) == 2
    assert len(data["edges"]) == 1


def test_export_empty_project(conn: sqlite3.Connection, config: dict) -> None:
    data = export("empty-project", conn)

    assert data["nodes"] == []
    assert data["edges"] == []
    assert data["events"] == []
    assert data["project"] == "empty-project"


def test_export_after_act(conn: sqlite3.Connection, config: dict) -> None:
    src = _make_node(conn)
    tgt = _make_node(conn)
    _edge(conn, src, tgt, "inspect")

    act(src, "inspect", conn, config, evidence="evidence")

    data = export("test", conn)

    assert len(data["nodes"]) == 2
    assert len(data["edges"]) == 1
    assert len(data["events"]) == 1
    assert data["events"][0]["node_id"] == src


def test_export_graphml(conn: sqlite3.Connection, config: dict) -> None:
    a = _make_node(conn, label="auth login")
    b = _make_node(conn, label="auth home")
    _edge(conn, a, b, "navigate")

    data = export("test", conn, fmt="graphml")

    assert data["format"] == "graphml"
    assert data["graphml"].startswith("<?xml")
    assert "<graphml" in data["graphml"]
    assert f'<node id="{a}"' in data["graphml"]
    assert f'<node id="{b}"' in data["graphml"]
    assert f'<edge source="{a}" target="{b}"' in data["graphml"]
    assert "auth login" in data["graphml"]
    assert "navigate" in data["graphml"]
    assert data["graphml"].endswith("</graphml>")
