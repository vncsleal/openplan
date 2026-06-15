from __future__ import annotations

import json
import sqlite3

import pytest

from openplan.core.state import branch, generate_id
from openplan.core.graph import diagnostics
from openplan.db.schema import init_db


@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


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


def test_diagnostics_empty_project(conn: sqlite3.Connection) -> None:
    _make_node(conn, "test", "Root")
    result = diagnostics("test", conn)

    assert result["project"] == "test"
    assert result["overview"]["states"] == 1
    assert result["overview"]["edges"] == 0
    assert result["overview"]["events"] == 0
    assert result["overview"]["max_depth"] == 0
    assert result["health"]["calibrated_edges"] == 0
    assert result["orphan_count"] == 0
    assert len(result["issues"]) == 1
    assert result["issues"][0]["code"] == "EMPTY_GRAPH"


def test_diagnostics_flat_tree(conn: sqlite3.Connection, config: dict) -> None:
    src = _make_node(conn, "test", "Root")
    opts = [
        {"label": "A", "action": "implement", "prob": 0.8, "expected_cost": {"tokens": 1000, "risk": 0.1}},
        {"label": "B", "action": "research", "prob": 0.7, "expected_cost": {"tokens": 2000, "risk": 0.2}},
        {"label": "C", "action": "review", "prob": 0.9, "expected_cost": {"tokens": 500, "risk": 0.05}},
    ]
    branch(src, opts, conn, config)

    result = diagnostics("test", conn)

    assert result["overview"]["states"] == 4
    assert result["overview"]["edges"] == 5
    assert result["overview"]["max_depth"] == 1  # BFS finds shortest path (root→child), not chain depth
    assert result["overview"]["leaf_states"] == 1
    assert result["overview"]["root_states"] == 1
    assert len(result["actions_used"]) == 3
    assert result["orphan_count"] == 1
    assert result["health"]["action_types"] == 3
    assert result["health"]["calibrated_edges"] == 0


def test_diagnostics_parallel_tree(conn: sqlite3.Connection, config: dict) -> None:
    src = _make_node(conn, "test", "Root")
    opts = [
        {"label": "A", "action": "implement", "prob": 0.8, "expected_cost": {"tokens": 1000, "risk": 0.1}},
        {"label": "B", "action": "research", "prob": 0.7, "expected_cost": {"tokens": 2000, "risk": 0.2}},
        {"label": "C", "action": "review", "prob": 0.9, "expected_cost": {"tokens": 500, "risk": 0.05}},
    ]
    branch(src, opts, conn, config, parallel=True)

    result = diagnostics("test", conn)

    assert result["overview"]["states"] == 4
    assert result["overview"]["edges"] == 3
    assert result["overview"]["max_depth"] == 1
    assert result["overview"]["leaf_states"] == 3
    assert result["orphan_count"] == 3


def test_diagnostics_deep_tree(conn: sqlite3.Connection, config: dict) -> None:
    r0 = _make_node(conn, "test", "Root")
    r1 = _make_node(conn, "test", "Level 1")
    r2 = _make_node(conn, "test", "Level 2")
    _ = _make_node(conn, "test", "Level 3")

    conn.execute(
        "INSERT INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob, created_at, updated_at) VALUES (?, ?, 'implement', 1000, 0.1, 0.8, datetime('now'), datetime('now'))",
        (r0, r1),
    )
    conn.execute(
        "INSERT INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob, created_at, updated_at) VALUES (?, ?, 'review', 2000, 0.2, 0.7, datetime('now'), datetime('now'))",
        (r1, r2),
    )

    result = diagnostics("test", conn)

    assert result["overview"]["states"] == 4
    assert result["overview"]["edges"] == 2
    assert result["overview"]["max_depth"] == 2
    assert result["overview"]["leaf_states"] == 2
    assert len(result["actions_used"]) == 2
    assert not any(i["code"] == "SHALLOW_GRAPH" for i in result["issues"])
