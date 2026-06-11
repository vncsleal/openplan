from __future__ import annotations

import sqlite3

import pytest

from openplan.core.activation import reset_cache
from openplan.core.graph import generate_id, plan
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


def _make_node(conn: sqlite3.Connection, project: str = "test", label: str = "") -> str:
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
    prob: float = 0.8,
    cost_tokens: float = 10000,
    cost_risk: float = 0.1,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob) VALUES (?, ?, ?, ?, ?, ?)",
        (source, target, action, cost_tokens, cost_risk, prob),
    )


def test_plan_pathfinding(conn: sqlite3.Connection, config: dict) -> None:
    s1 = _make_node(conn)
    s2 = _make_node(conn)
    s3 = _make_node(conn)
    s4 = _make_node(conn)

    _edge(conn, s1, s2, "implement", prob=0.9, cost_tokens=10000)
    _edge(conn, s2, s3, "review", prob=0.85, cost_tokens=5000)
    _edge(conn, s3, s4, "deploy", prob=0.95, cost_tokens=2000)

    result = plan(s1, s4, conn, config)

    assert result["ok"] is True
    assert result["path"] == [s1, s2, s3, s4]
    assert len(result["traversal"]) == 3
    assert result["truncated"] is False
    assert result["expected_cost"]["steps"] == 3
    assert result["expected_cost"]["tokens"] == 17000
    assert result["expected_cost"]["risk"] == 0.1


def test_plan_respects_constraints(conn: sqlite3.Connection, config: dict) -> None:
    start = _make_node(conn)
    mid = _make_node(conn)
    target = _make_node(conn)

    cheap = _make_node(conn)
    expensive = _make_node(conn)

    _edge(conn, start, cheap, "choose", prob=0.9, cost_tokens=1000)
    _edge(conn, cheap, target, "finish", prob=0.95, cost_tokens=500)

    _edge(conn, start, expensive, "choose", prob=0.9, cost_tokens=100000)
    _edge(conn, expensive, target, "finish", prob=0.95, cost_tokens=50000)

    # Constrain by max_cost — cheap path only (cost = 1500)
    result = plan(start, target, conn, config, constraints={"max_cost": 5000})

    assert result["ok"] is True
    assert cheap in result["path"]
    assert expensive not in result["path"]

    # Constrain by min_prob — both paths have high prob, so both work
    result2 = plan(start, target, conn, config, constraints={"min_prob": 0.5})
    assert result2["ok"] is True


def test_plan_expansion_limit(conn: sqlite3.Connection, config: dict) -> None:
    nodes = [_make_node(conn) for _ in range(20)]
    source = nodes[0]
    target = nodes[-1]

    # Create a chain through all nodes so Dijkstra must explore many
    for i in range(len(nodes) - 1):
        _edge(conn, nodes[i], nodes[i + 1], "step", prob=0.9, cost_tokens=1000)

    # With a very small expansion limit, the search should be truncated
    result = plan(source, target, conn, config, constraints={"expansion_limit": 3})

    assert result["ok"] is True
    assert result["truncated"] is True


def test_plan_high_uncertainty_flag(conn: sqlite3.Connection, config: dict) -> None:
    s1 = _make_node(conn)
    s2 = _make_node(conn)
    s3 = _make_node(conn)

    # Edge with prob < 0.5 — high uncertainty
    _edge(conn, s1, s2, "risky", prob=0.3, cost_tokens=1000)
    _edge(conn, s2, s3, "finish", prob=0.9, cost_tokens=500)

    result = plan(s1, s3, conn, config)

    assert result["ok"] is True
    assert result["high_uncertainty"] is True
    assert result["truncated"] is False


def test_plan_no_path(conn: sqlite3.Connection, config: dict) -> None:
    s1 = _make_node(conn)
    s2 = _make_node(conn)

    _edge(conn, s1, _make_node(conn), "some_action")

    result = plan(s1, s2, conn, config)

    assert result["ok"] is False
    assert result["error"]["code"] == "NO_PATH"
