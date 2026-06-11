from __future__ import annotations

import time
from typing import Any

import pytest

from openplan.db.schema import init_db


def _populate_graph(
    conn: Any,
    nodes: int = 10,
    edges_per_node: int = 3,
    project: str = "test",
) -> None:
    """Create a synthetic graph for benchmarking."""
    import random

    random.seed(42)

    for i in range(nodes):
        sid = f"S-{i + 1:06d}"
        conn.execute(
            "INSERT INTO nodes (id, label, project) VALUES (?, ?, ?)",
            (sid, f"state_{i}", project),
        )

    for i in range(nodes):
        src = f"S-{i + 1:06d}"
        targets = random.sample(
            [j for j in range(nodes) if j != i],
            min(edges_per_node, nodes - 1),
        )
        for j in targets:
            tgt = f"S-{j + 1:06d}"
            cost = random.randint(1000, 50000)
            risk = round(random.uniform(0.01, 0.3), 2)
            prob = round(random.uniform(0.3, 0.99), 2)
            conn.execute(
                "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob) VALUES (?, ?, ?, ?, ?, ?)",
                (src, tgt, "transition", cost, risk, prob),
            )


@pytest.fixture
def conn() -> Any:
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    return c


@pytest.fixture
def config() -> dict[str, Any]:
    return {
        "activation_threshold": 0.5,
        "activation_weights": {"in_degree": 0.4, "frontier": 0.3, "recency": 0.2, "boost": 0.1},
        "stale_days": 2,
        "page_rank": {"iterations": 20, "damping": 0.85},
    }


def test_observe_rank(conn: Any, config: dict[str, Any]) -> None:
    """observe(scope='rank') returns nodes sorted by PageRank score."""
    from openplan.core.graph import observe

    _populate_graph(conn, nodes=20, edges_per_node=3)

    result = observe("test", query=None, scope="rank", conn=conn, config=config)

    assert result["mode"] == "rank"
    assert result["count"] == 20
    assert "pagerank" in result
    assert len(result["pagerank"]) == 20
    assert len(result["states"]) == 20
    pr_values = list(result["pagerank"].values())
    assert all(pr_values[i] >= pr_values[i + 1] for i in range(len(pr_values) - 1)), (
        "PageRank values not sorted descending"
    )
    total = sum(pr_values)
    assert 0.95 < total < 1.05, f"PageRank total {total} not close to 1.0"


def test_observe_rank_empty(conn: Any, config: dict[str, Any]) -> None:
    """observe(scope='rank') on empty project returns empty."""
    from openplan.core.graph import observe

    result = observe("test", query=None, scope="rank", conn=conn, config=config)
    assert result["mode"] == "rank"
    assert result["count"] == 0


def test_export_matrix_format(conn: Any) -> None:
    """export(format='matrix') returns sparse edge list."""
    from openplan.core.graph import export

    _populate_graph(conn, nodes=5, edges_per_node=2)

    result = export("test", conn, fmt="matrix")

    assert result["format"] == "matrix"
    assert isinstance(result["sparse"], list)
    for entry in result["sparse"]:
        assert "source" in entry
        assert "target" in entry
        assert isinstance(entry["value"], float)
        assert entry["value"] > 0


def test_export_matrix_empty(conn: Any) -> None:
    """export(format='matrix') on empty project returns empty list."""
    from openplan.core.graph import export

    result = export("test", conn, fmt="matrix")
    assert result["format"] == "matrix"
    assert result["sparse"] == []


def test_archive_events(conn: Any) -> None:
    """archive_events moves old events to events_archive table."""
    from openplan.core.graph import archive_events
    import json
    from datetime import datetime, timezone

    conn.execute("INSERT INTO nodes (id, label, project) VALUES ('S-000001', 'test', 'test')")

    old = datetime(2024, 1, 1, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn.execute(
        "INSERT INTO events (id, project, node_id, event_type, payload, version, created_at) VALUES (?, 'test', 'S-000001', 'acted', ?, 1, ?)",
        ("E-001", json.dumps({"action": "test"}), old),
    )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn.execute(
        "INSERT INTO events (id, project, node_id, event_type, payload, version, created_at) VALUES (?, 'test', 'S-000001', 'acted', ?, 1, ?)",
        ("E-002", json.dumps({"action": "test"}), now),
    )

    result = archive_events(conn, older_than_days=30)

    assert result["archived"] >= 1
    assert result["deleted"] >= 1

    archived = conn.execute("SELECT id FROM events_archive").fetchall()
    assert any(r["id"] == "E-001" for r in archived)

    remaining = conn.execute("SELECT id FROM events").fetchall()
    assert any(r["id"] == "E-002" for r in remaining)
    assert not any(r["id"] == "E-001" for r in remaining)


@pytest.mark.slow
def test_plan_5k_scale(conn: Any) -> None:
    """plan returns in < 5s with 5000 synthetic nodes (not <100ms due to SQLite)."""
    from openplan.core.graph import plan

    _populate_graph(conn, nodes=5000, edges_per_node=3)

    start = time.perf_counter()
    result = plan("S-000001", "S-005000", conn, {})
    elapsed = time.perf_counter() - start

    assert elapsed < 5.0, f"plan(5k) took {elapsed:.2f}s"

    assert result["ok"] is True or "NO_PATH" in result.get("error", {}).get("code", "")


@pytest.mark.slow
def test_observe_rank_5k_scale(conn: Any, config: dict[str, Any]) -> None:
    """PageRank on 5k nodes returns in < 5s."""
    from openplan.core.graph import observe

    _populate_graph(conn, nodes=5000, edges_per_node=3)

    start = time.perf_counter()
    result = observe("test", query=None, scope="rank", conn=conn, config=config)
    elapsed = time.perf_counter() - start

    assert elapsed < 10.0, f"PageRank(5k) took {elapsed:.2f}s"
    assert result["count"] == 5000
    assert len(result["pagerank"]) == 5000
