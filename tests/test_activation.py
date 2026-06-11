from __future__ import annotations

import json
import sqlite3

import pytest

from openplan.core.activation import get_activation, reset_cache
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
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(id, 3) AS INTEGER)) AS max_id FROM nodes WHERE project = ?",
        (project,),
    ).fetchone()
    next_num = (row["max_id"] or 0) + 1
    sid = f"S-{next_num:06d}"
    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES (?, ?, ?)", (sid, label, project)
    )
    return sid


def _edge(
    conn: sqlite3.Connection, source: str, target: str, action: str = "transition"
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob) VALUES (?, ?, ?, 10000, 0.1, 0.8)",
        (source, target, action),
    )


def test_activation_in_degree(conn: sqlite3.Connection, config: dict) -> None:
    a = _make_node(conn)
    b = _make_node(conn)
    sources = [_make_node(conn) for _ in range(5)]
    single_source = _make_node(conn)

    for s in sources:
        _edge(conn, s, a)
    _edge(conn, single_source, b)

    act_a = get_activation(a, conn, config)
    act_b = get_activation(b, conn, config)

    assert act_a > act_b, f"Expected {a} ({act_a}) > {b} ({act_b})"


def test_activation_frontier_ratio(conn: sqlite3.Connection, config: dict) -> None:
    resolved_targets = [_make_node(conn) for _ in range(3)]
    unresolved_sources = [_make_node(conn) for _ in range(5)]
    unresolved_targets = [_make_node(conn) for _ in range(3)]

    high = _make_node(conn, label="high-frontier")
    low = _make_node(conn, label="low-frontier")

    for u in unresolved_targets:
        for s in unresolved_sources:
            _edge(conn, s, u)

    for rt in resolved_targets:
        _edge(conn, low, rt)
    for ut in unresolved_targets:
        _edge(conn, high, ut)

    for n in resolved_targets + unresolved_targets:
        get_activation(n, conn, config)

    act_low = get_activation(low, conn, config)
    act_high = get_activation(high, conn, config)

    assert act_high > act_low, f"Expected high ({act_high}) > low ({act_low})"


def test_activation_recency(conn: sqlite3.Connection, config: dict) -> None:
    recent = _make_node(conn)
    old = _make_node(conn)

    conn.execute(
        "UPDATE nodes SET updated_at = '2020-01-01T00:00:00.000Z' WHERE id = ?",
        (old,),
    )

    act_recent = get_activation(recent, conn, config)
    act_old = get_activation(old, conn, config)

    assert act_recent > act_old, f"Expected recent ({act_recent}) > old ({act_old})"


def test_activation_boost(conn: sqlite3.Connection, config: dict) -> None:
    boosted = _make_node(conn)
    normal = _make_node(conn)

    conn.execute(
        "UPDATE nodes SET props = ? WHERE id = ?",
        (json.dumps({"boost": True}), boosted),
    )

    act_boosted = get_activation(boosted, conn, config)
    act_normal = get_activation(normal, conn, config)

    assert act_boosted > act_normal, f"Expected boosted ({act_boosted}) > normal ({act_normal})"


def test_activation_cache_invalidation(conn: sqlite3.Connection, config: dict) -> None:
    from openplan.core.activation import mark_dirty as md

    a = _make_node(conn)
    b = _make_node(conn)
    c = _make_node(conn)

    _edge(conn, a, b)
    _edge(conn, b, c)

    get_activation(c, conn, config)
    get_activation(b, conn, config)
    act_before = get_activation(a, conn, config)

    extra = [_make_node(conn) for _ in range(5)]
    for s in extra:
        _edge(conn, s, b)
    md(b, conn)
    md(a, conn)

    act_after = get_activation(a, conn, config)

    assert act_after != act_before, (
        f"Expected activation changed after invalidation ({act_before} -> {act_after})"
    )
