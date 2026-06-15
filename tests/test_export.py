from __future__ import annotations

import json
import sqlite3

import pytest

from openplan.core.activation import reset_cache
from openplan.core.state import act, generate_id, branch
from openplan.core.export import export, prune
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
    assert isinstance(data["version"], str) and data["version"]
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


def test_prune_with_evidence(conn: sqlite3.Connection, config: dict) -> None:
    src = generate_id("test", conn)
    conn.execute("INSERT INTO nodes (id, label, project) VALUES (?, ?, ?)", (src, "Root", "test"))
    child = generate_id("test", conn)
    conn.execute("INSERT INTO nodes (id, label, project) VALUES (?, ?, ?)", (child, "Child", "test"))
    gchild = generate_id("test", conn)
    conn.execute("INSERT INTO nodes (id, label, project) VALUES (?, ?, ?)", (gchild, "Grandchild", "test"))
    conn.execute(
        "INSERT INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob) VALUES (?, ?, ?, ?, ?, ?)",
        (src, child, "implement", 1000, 0.1, 0.8),
    )
    conn.execute(
        "INSERT INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob) VALUES (?, ?, ?, ?, ?, ?)",
        (child, gchild, "implement", 1000, 0.1, 0.8),
    )
    conn.execute("UPDATE nodes SET status = 'done' WHERE id IN (?, ?)", (child, gchild))
    conn.execute(
        "INSERT INTO evidence (id, project, state_id, evidence_type, uri, description, status, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("EV-001", "test", gchild, "file", "/tmp/test.txt", "evidence on grandchild", "verified", "{}"),
    )
    conn.commit()

    result = prune(child, conn, config)
    assert result["ok"] is True
    assert result["collapsed_nodes"] == 1  # grandchild collapsed

    gchild_remaining = conn.execute("SELECT id FROM nodes WHERE id = ?", (gchild,)).fetchone()
    assert gchild_remaining is None, "grandchild should be deleted"

    ev_remaining = conn.execute("SELECT id FROM evidence WHERE state_id = ?", (gchild,)).fetchone()
    assert ev_remaining is None, "evidence for grandchild should be deleted"

    child_remaining = conn.execute("SELECT label, status FROM nodes WHERE id = ?", (child,)).fetchone()
    assert child_remaining is not None, "pruned state itself should remain (renamed)"
    assert child_remaining["status"] == "done"


def test_version_consistency(conn: sqlite3.Connection, config: dict) -> None:
    from openplan import VERSION
    data = export("test", conn)
    assert data["version"] == VERSION, "export version should match module version"


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
