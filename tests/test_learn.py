from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import pytest

from openplan.core.ids import generate_event_id
from openplan.core.planner import learn
from openplan.db.schema import init_db


@pytest.fixture
def conn() -> sqlite3.Connection:
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
        "learning": {
            "smoothing_factor": 0.3,
            "min_acts_for_calibration": 3,
        },
    }


def _make_state(conn: sqlite3.Connection, sid: str, label: str = "") -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn.execute(
        "INSERT INTO nodes (id, label, project, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (sid, label, "test", now, now),
    )


def _make_edge(
    conn: sqlite3.Connection,
    src: str,
    tgt: str,
    action: str = "transition",
    cost_tokens: float = 10000,
    cost_risk: float = 0.1,
    prob: float = 0.8,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn.execute(
        "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (src, tgt, action, cost_tokens, cost_risk, prob, now, now),
    )


def _make_event(
    conn: sqlite3.Connection,
    node_id: str,
    target: str,
    action: str = "transition",
    actual_tokens: float = 8000,
    actual_risk: float = 0.05,
    expected_tokens: float | None = 10000,
    expected_risk: float | None = 0.1,
) -> None:
    eid = generate_event_id("test", conn)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    payload = {
        "action": action,
        "source": node_id,
        "target": target,
        "cost_actual": {"tokens": actual_tokens, "risk": actual_risk},
    }
    if expected_tokens is not None:
        payload["expected_cost"] = {"tokens": expected_tokens, "risk": expected_risk}
    conn.execute(
        "INSERT INTO events (id, project, node_id, event_type, payload, version, idempotency_key, session_id, created_at) VALUES (?, 'test', ?, 'acted', ?, 1, '', '', ?)",
        (eid, node_id, json.dumps(payload), now),
    )


def test_learn_adjusts_weights(conn: sqlite3.Connection, config: dict[str, Any]) -> None:
    """learn appends weight_history entry with correct delta."""
    _make_state(conn, "S-000001", "alpha")
    _make_state(conn, "S-000002", "beta")
    _make_edge(conn, "S-000001", "S-000002", "implement", cost_tokens=10000)
    _make_event(conn, "S-000001", "S-000002", "implement", actual_tokens=8000, actual_risk=0.05)

    result = learn("S-000001", "S-000002", "success", 8000, conn, config)

    assert result["ok"] is True
    assert result["edge"]["action"] == "implement"
    assert result["calibration"]["delta"] == -2000
    assert result["calibration"]["history_length"] == 1

    row = conn.execute(
        "SELECT weight_history FROM edges WHERE source_id = 'S-000001' AND target_id = 'S-000002' AND action = 'implement'"
    ).fetchone()
    wh = json.loads(row["weight_history"])
    assert len(wh) == 1
    assert wh[0]["delta"]["tokens"] == -2000


def test_learn_calibrates_estimates(conn: sqlite3.Connection, config: dict[str, Any]) -> None:
    """After min_acts_for_calibration, cost_tokens is adjusted toward actual average."""
    config["learning"]["min_acts_for_calibration"] = 2

    _make_state(conn, "S-000001", "alpha")
    _make_state(conn, "S-000002", "beta")
    _make_edge(conn, "S-000001", "S-000002", "implement", cost_tokens=10000)

    _make_event(conn, "S-000001", "S-000002", "implement", actual_tokens=5000, expected_tokens=10000)
    result1 = learn("S-000001", "S-000002", "success", 5000, conn, config)
    assert result1["calibration"]["new_cost"] == 10000

    _make_event(conn, "S-000001", "S-000002", "implement", actual_tokens=7000, expected_tokens=10000)
    result2 = learn("S-000001", "S-000002", "success", 7000, conn, config)

    assert result2["calibration"]["new_cost"] == 8800.0
    assert result2["calibration"]["delta"] == -3000

    row = conn.execute(
        "SELECT cost_tokens FROM edges WHERE source_id = 'S-000001' AND target_id = 'S-000002' AND action = 'implement'"
    ).fetchone()
    assert row["cost_tokens"] == 8800.0


def test_learn_resolves_edge(conn: sqlite3.Connection, config: dict[str, Any]) -> None:
    """learn resolves the correct edge when multiple actions exist between same states."""
    _make_state(conn, "S-000001", "alpha")
    _make_state(conn, "S-000002", "beta")

    _make_edge(conn, "S-000001", "S-000002", "implement", cost_tokens=10000)
    _make_edge(conn, "S-000001", "S-000002", "verify", cost_tokens=5000)

    _make_event(conn, "S-000001", "S-000002", "implement", actual_tokens=8000)

    result = learn("S-000001", "S-000002", "success", 8000, conn, config)

    assert result["ok"] is True
    assert result["edge"]["action"] == "implement"
    assert result["calibration"]["previous_cost"] == 10000

    verify_row = conn.execute(
        "SELECT weight_history FROM edges WHERE source_id = 'S-000001' AND target_id = 'S-000002' AND action = 'verify'"
    ).fetchone()
    assert verify_row["weight_history"] == "[]"

    impl_row = conn.execute(
        "SELECT weight_history FROM edges WHERE source_id = 'S-000001' AND target_id = 'S-000002' AND action = 'implement'"
    ).fetchone()
    wh = json.loads(impl_row["weight_history"])
    assert len(wh) == 1
