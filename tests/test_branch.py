from __future__ import annotations

import json
import sqlite3

import pytest

import pytest

from openplan.core.activation import get_activation, reset_cache
from openplan.core.errors import InvalidStateError
from openplan.core.state import branch, generate_id
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


def test_branch_creates_states(conn: sqlite3.Connection, config: dict) -> None:
    src = _make_node(conn)
    options = [
        {"label": "Option A", "action": "implement", "prob": 0.8, "expected_cost": {"tokens": 10000, "risk": 0.1}},
        {"label": "Option B", "action": "research", "prob": 0.9, "expected_cost": {"tokens": 20000, "risk": 0.2}},
        {"label": "Option C", "action": "review", "prob": 0.7, "expected_cost": {"tokens": 15000, "risk": 0.15}},
    ]

    result = branch(src, options, conn, config)

    assert result["ok"] is True
    assert result["options"] == 3
    assert len(result["states_created"]) == 3

    for sid in result["states_created"]:
        row = conn.execute("SELECT * FROM nodes WHERE id = ?", (sid,)).fetchone()
        assert row is not None
        props = json.loads(row["props"])
        assert props["boost"] is True
        assert "boosted_at" in props

    assert result["branch_id"].startswith("B-")


def test_branch_links_probabilities(conn: sqlite3.Connection, config: dict) -> None:
    src = _make_node(conn)
    options = [
        {"label": "Fast path", "action": "implement", "prob": 0.8, "expected_cost": {"tokens": 5000, "risk": 0.1}},
        {"label": "Safe path", "action": "review", "prob": 0.99, "expected_cost": {"tokens": 50000, "risk": 0.05}},
    ]

    result = branch(src, options, conn, config)

    edges = conn.execute(
        "SELECT * FROM edges WHERE source_id = ? ORDER BY cost_tokens", (src,)
    ).fetchall()
    assert len(edges) == 2

    assert edges[0]["target_id"] == result["states_created"][0]
    assert edges[0]["action"] == "implement"
    assert edges[0]["prob"] == 0.8
    assert edges[0]["cost_tokens"] == 5000
    assert edges[0]["cost_risk"] == 0.1

    assert edges[1]["target_id"] == result["states_created"][1]
    assert edges[1]["action"] == "review"
    assert edges[1]["prob"] == 0.99
    assert edges[1]["cost_tokens"] == 50000
    assert edges[1]["cost_risk"] == 0.05


def test_branch_records_event(conn: sqlite3.Connection, config: dict) -> None:
    src = _make_node(conn)
    options = [
        {"label": "Option A", "action": "implement", "prob": 0.8, "expected_cost": {"tokens": 10000, "risk": 0.1}},
    ]

    branch(src, options, conn, config)

    events = conn.execute("SELECT * FROM events").fetchall()
    assert len(events) == 1
    assert events[0]["event_type"] == "branched"
    assert events[0]["node_id"] == src
    payload = json.loads(events[0]["payload"])
    assert payload["action"] == "branched"
    assert payload["branch_id"].startswith("B-")
    assert len(payload["states_created"]) == 1


def test_branch_invalid_state(conn: sqlite3.Connection, config: dict) -> None:
    with pytest.raises(InvalidStateError):
        branch("S-999999", [{"label": "Test", "action": "implement", "prob": 0.8}], conn, config)


def test_branch_sequenced_options_chain_states(conn: sqlite3.Connection, config: dict) -> None:
    src = _make_node(conn, project="test", label="Root")
    options = [
        {"label": "Step A", "action": "implement", "sequence": 1, "expected_cost": {"tokens": 5000, "risk": 0.1}},
        {"label": "Step C", "action": "implement", "sequence": 3, "expected_cost": {"tokens": 5000, "risk": 0.1}},
        {"label": "Step B", "action": "implement", "sequence": 2, "expected_cost": {"tokens": 5000, "risk": 0.1}},
    ]

    result = branch(src, options, conn, config)

    assert result["ok"] is True
    assert len(result["states_created"]) == 3

    a_id = result["states_created"][0]
    b_id = result["states_created"][1]
    c_id = result["states_created"][2]

    edges_from_a = conn.execute(
        "SELECT target_id, action FROM edges WHERE source_id = ?", (a_id,)
    ).fetchall()
    edges_from_b = conn.execute(
        "SELECT target_id, action FROM edges WHERE source_id = ?", (b_id,)
    ).fetchall()
    edges_from_c = conn.execute(
        "SELECT target_id, action FROM edges WHERE source_id = ?", (c_id,)
    ).fetchall()

    assert any(e["target_id"] == b_id for e in edges_from_a), "A should have edge to B"
    assert any(e["target_id"] == c_id for e in edges_from_b), "B should have edge to C"
    assert len(edges_from_c) == 2, "C should have src edge only (no next in chain)"

    edges_from_src = conn.execute(
        "SELECT target_id FROM edges WHERE source_id = ?", (src,)
    ).fetchall()
    assert len(edges_from_src) == 3, "All 3 options still linked from source"
