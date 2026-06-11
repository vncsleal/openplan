from __future__ import annotations

import json
import sqlite3

import pytest

from openplan.core.activation import get_activation, reset_cache
from openplan.core.state import generate_id
from openplan.core.graph import observe
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


def test_observe_frontier(conn: sqlite3.Connection, config: dict) -> None:
    a = _make_node(conn)
    b = _make_node(conn)
    c = _make_node(conn)

    _edge(conn, a, b)
    _edge(conn, b, c)

    for n in (a, b, c):
        get_activation(n, conn, config)

    result = observe("test", None, "frontier", conn, config)

    assert result["mode"] == "frontier"
    ids = [s["id"] for s in result["states"]]
    assert a in ids
    assert b in ids


def test_observe_all(conn: sqlite3.Connection, config: dict) -> None:
    a = _make_node(conn)
    b = _make_node(conn)

    conn.execute(
        "UPDATE nodes SET props = ? WHERE id = ?",
        (json.dumps({"boost": True}), a),
    )
    get_activation(a, conn, config)
    get_activation(b, conn, config)

    result = observe("test", None, "all", conn, config)

    assert result["mode"] == "all"
    ids = [s["id"] for s in result["states"]]
    assert ids == [a, b]


def test_observe_query_fts5(conn: sqlite3.Connection, config: dict) -> None:
    _make_node(conn, label="alpha state")
    _make_node(conn, label="beta state")
    _make_node(conn, label="gamma ray")

    result = observe("test", "alpha", None, conn, config)

    assert result["mode"] == "similarity"
    labels = [s["label"] for s in result["states"]]
    assert "alpha state" in labels
    if result.get("method") == "fts5":
        assert "beta state" not in labels


def test_observe_cluster(conn: sqlite3.Connection, config: dict) -> None:
    """Cluster scope groups states by activation bucket + label prefix."""
    _make_node(conn, label="auth login")
    _make_node(conn, label="auth register")
    _make_node(conn, label="billing plan")

    result = observe("test", None, "cluster", conn, config)
    assert result["mode"] == "cluster"
    assert result["cluster_count"] == 2
    keys = list(result["clusters"].keys())
    assert any("auth" in k for k in keys)
    assert any("billing" in k for k in keys)
    auth_key = next(k for k in keys if "auth" in k)
    assert len(result["clusters"][auth_key]) == 2


def test_observe_empty_project(conn: sqlite3.Connection, config: dict) -> None:
    result = observe("nonexistent", None, "frontier", conn, config)

    assert result["mode"] == "frontier"
    assert result["states"] == []
    assert result["graph"]["node_count"] == 0
    assert result["graph"]["edge_count"] == 0
    assert result["recommended"] is None


def test_observe_surfaces_health_issues(conn: sqlite3.Connection, config: dict) -> None:
    """observe includes graph health issues when the project has structural problems."""
    src = _make_node(conn, "health-test", "Root")
    for i in range(3):
        child = _make_node(conn, "health-test", f"leaf-{i}")
        conn.execute(
            "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob) VALUES (?, ?, 'implement', 1000, 0.1, 0.8)",
            (src, child),
        )
    get_activation(src, conn, config)
    for r in conn.execute("SELECT id FROM nodes WHERE project = 'health-test'").fetchall():
        get_activation(r["id"], conn, config)

    result = observe("health-test", None, "frontier", conn, config)

    assert "health" in result["graph"]
    assert result["graph"]["health"] is not None
    assert result["graph"]["health"]["orphan_count"] == 3
    code = result["graph"]["health"]["issues"][0]["code"]
    assert code == "HIGH_ORPHAN_COUNT"
