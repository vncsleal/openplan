from __future__ import annotations

import json
import sqlite3

import pytest

import pytest

from openplan.core.activation import reset_cache
from openplan.core.errors import InvalidActionError, InvalidStateError
from openplan.core.state import act
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


def _make_node(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(id, 3) AS INTEGER)) AS max_id FROM nodes"
    ).fetchone()
    next_num = (row["max_id"] or 0) + 1
    sid = f"S-{next_num:06d}"
    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES (?, ?, ?)", (sid, "", "test")
    )
    return sid


def _edge(
    conn: sqlite3.Connection,
    source: str,
    target: str,
    action: str = "transition",
    prob: float = 0.8,
    cost_tokens: float = 10000,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob) VALUES (?, ?, ?, ?, 0.1, ?)",
        (source, target, action, cost_tokens, prob),
    )


def test_act_transition(conn: sqlite3.Connection, config: dict) -> None:
    src = _make_node(conn)
    tgt = _make_node(conn)
    _edge(conn, src, tgt, "inspect")

    result = act(src, "inspect", conn, config, evidence="evidence-123", thought="thoughtful")

    assert result["ok"] is True
    assert result["next_state"] == tgt
    assert result["cursor"] == tgt
    assert result["cost_actual"]["tokens"] == 10000
    assert result["cost_actual"]["risk"] == 0.1
    assert result["cost_delta"] is None
    assert isinstance(result["new_frontier"], list)

    events = conn.execute("SELECT * FROM events").fetchall()
    assert len(events) == 1
    payload = json.loads(events[0]["payload"])
    assert payload["action"] == "inspect"
    assert payload["evidence"] == "evidence-123"
    assert payload["thought"] == "thoughtful"


def test_act_cost_delta(conn: sqlite3.Connection, config: dict) -> None:
    src = _make_node(conn)
    tgt = _make_node(conn)
    _edge(conn, src, tgt, "inspect", cost_tokens=8000)

    result = act(src, "inspect", conn, config, expected_cost={"tokens": 5000, "risk": 0.05})
    assert result["ok"] is True
    assert result["cost_delta"] == {"tokens": 3000, "risk": 0.05}

    result2 = act(src, "inspect", conn, config)
    assert result2["ok"] is True
    assert result2["cost_delta"] is None
    src = _make_node(conn)
    tgt_a = _make_node(conn)
    tgt_b = _make_node(conn)
    _edge(conn, src, tgt_a, "review", prob=0.5)
    _edge(conn, src, tgt_b, "review", prob=0.9)

    result = act(src, "review", conn, config)

    assert result["ok"] is True
    assert result["next_state"] == tgt_b


def test_act_invalid_state(conn: sqlite3.Connection, config: dict) -> None:
    with pytest.raises(InvalidStateError):
        act("S-999999", "inspect", conn, config)


def test_act_invalid_action(conn: sqlite3.Connection, config: dict) -> None:
    src = _make_node(conn)
    tgt = _make_node(conn)
    _edge(conn, src, tgt, "inspect")

    with pytest.raises(InvalidActionError):
        act(src, "nonexistent", conn, config)
