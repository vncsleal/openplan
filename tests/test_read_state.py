from __future__ import annotations

import json
import sqlite3

import pytest

from openplan.core.errors import InvalidStateError, InvalidStatusError
from openplan.core.read import read_state, reconstruct, update_state
from openplan.core.reasoning import ReasoningPayload
from openplan.db.schema import init_db


@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    yield conn
    conn.close()


def _make_node(conn: sqlite3.Connection, project: str = "test", label: str = "test state", props: str = "{}") -> str:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(id, 3) AS INTEGER)) AS max_id FROM nodes"
    ).fetchone()
    next_num = (row["max_id"] or 0) + 1
    sid = f"S-{next_num:06d}"
    conn.execute(
        "INSERT INTO nodes (id, label, project, props) VALUES (?, ?, ?, ?)", (sid, label, project, props)
    )
    return sid


def _edge(conn: sqlite3.Connection, source: str, target: str, action: str = "transition", prob: float = 0.8, cost_tokens: float = 10000) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob) VALUES (?, ?, ?, ?, 0.1, ?)",
        (source, target, action, cost_tokens, prob),
    )


def test_read_state_basic(conn: sqlite3.Connection) -> None:
    sid = _make_node(conn)
    result = read_state(sid, conn)
    assert result["ok"] is True
    assert result["state"]["id"] == sid
    assert result["state"]["status"] == "pending"
    assert result["state"]["frontier"] is False
    assert result["edges_out"] == []
    assert result["edges_in"] == []
    assert result["events"] == []


def test_read_state_with_reasoning(conn: sqlite3.Connection) -> None:
    props = json.dumps({"type": "decision", "question": "test?", "visit_count": 3})
    sid = _make_node(conn, props=props)
    result = read_state(sid, conn)
    assert result["state"]["reasoning"]["type"] == "decision"
    assert result["state"]["reasoning"]["question"] == "test?"
    assert result["state"]["props"]["visit_count"] == 3
    assert "type" not in result["state"]["props"]


def test_read_state_with_edges(conn: sqlite3.Connection) -> None:
    a = _make_node(conn, label="A")
    b = _make_node(conn, label="B")
    _edge(conn, a, b)
    result = read_state(a, conn)
    assert len(result["edges_out"]) == 1
    assert result["edges_out"][0]["target_id"] == b
    assert result["edges_out"][0]["target_label"] == "B"

    result_b = read_state(b, conn)
    assert len(result_b["edges_in"]) == 1
    assert result_b["edges_in"][0]["source_id"] == a


def test_read_state_not_found(conn: sqlite3.Connection) -> None:
    with pytest.raises(InvalidStateError):
        read_state("S-999999", conn)


def test_update_state_status(conn: sqlite3.Connection) -> None:
    sid = _make_node(conn)
    result = update_state(sid, conn, status="done")
    assert result["ok"] is True
    assert result["state_id"] == sid
    assert "status" in result["updated_fields"]

    read = read_state(sid, conn)
    assert read["state"]["status"] == "done"


def test_update_state_props(conn: sqlite3.Connection) -> None:
    sid = _make_node(conn)
    patch = {"conclusion": "it worked", "tags": ["fix"]}
    result = update_state(sid, conn, props_patch=patch)
    assert "conclusion" in result["updated_fields"]

    read = read_state(sid, conn)
    assert read["state"]["reasoning"]["conclusion"] == "it worked"
    assert read["state"]["reasoning"]["tags"] == ["fix"]


def test_update_state_merge_preserves_visit(conn: sqlite3.Connection) -> None:
    props = json.dumps({"visit_count": 5})
    sid = _make_node(conn, props=props)
    update_state(sid, conn, props_patch={"conclusion": "merged"})
    read = read_state(sid, conn)
    assert read["state"]["props"]["visit_count"] == 5
    assert read["state"]["reasoning"]["conclusion"] == "merged"


def test_update_state_invalid_status(conn: sqlite3.Connection) -> None:
    sid = _make_node(conn)
    with pytest.raises(InvalidStatusError):
        update_state(sid, conn, status="nonexistent")


def test_update_state_not_found(conn: sqlite3.Connection) -> None:
    with pytest.raises(InvalidStateError):
        update_state("S-999999", conn, status="done")


def test_update_state_records_event(conn: sqlite3.Connection) -> None:
    sid = _make_node(conn)
    update_state(sid, conn, status="blocked", props_patch={"reason": "waiting on review"})
    events = conn.execute("SELECT * FROM events WHERE node_id = ?", (sid,)).fetchall()
    assert len(events) >= 1
    assert events[0]["event_type"] == "updated"


def test_reconstruct_basic(conn: sqlite3.Connection) -> None:
    a = _make_node(conn, project="p1", label="root")
    b = _make_node(conn, project="p1", label="child")
    _edge(conn, a, b)

    result = reconstruct("p1", conn)
    assert result["ok"] is True
    assert result["project"] == "p1"
    assert result["root"]["id"] == a
    assert result["root"]["label"] == "root"
    assert len(result["recent_path"]) == 0
    assert result["project_health"]["total_states"] == 2
    assert result["project_health"]["edge_count"] == 1


def test_reconstruct_status_counts(conn: sqlite3.Connection) -> None:
    a = _make_node(conn, project="p1")
    b = _make_node(conn, project="p1")
    c = _make_node(conn, project="p1")
    conn.execute("UPDATE nodes SET status = 'done' WHERE id = ?", (b,))
    conn.execute("UPDATE nodes SET status = 'done' WHERE id = ?", (c,))

    result = reconstruct("p1", conn)
    assert result["project_health"]["total_states"] == 3
    assert result["project_health"]["completed"] == 2
    assert result["project_health"]["pct_complete"] == 66.7


def test_reconstruct_empty_project(conn: sqlite3.Connection) -> None:
    result = reconstruct("empty", conn)
    assert result["ok"] is True
    assert result["root"] is None
    assert result["frontier"] == []
    assert result["project_health"]["total_states"] == 0


def test_reconstruct_with_recent_path(conn: sqlite3.Connection) -> None:
    a = _make_node(conn, project="p1", label="start")
    b = _make_node(conn, project="p1", label="middle")
    c = _make_node(conn, project="p1", label="end")
    _edge(conn, a, b, "implement")
    _edge(conn, b, c, "research")
    conn.execute(
        "INSERT INTO events (id, project, node_id, event_type, payload, version) VALUES (?, ?, ?, 'acted', ?, 1)",
        ("E-0001", "p1", a, json.dumps({"action": "implement", "source": a, "target": b})),
    )
    conn.execute(
        "INSERT INTO events (id, project, node_id, event_type, payload, version) VALUES (?, ?, ?, 'acted', ?, 1)",
        ("E-0002", "p1", b, json.dumps({"action": "research", "source": b, "target": c})),
    )

    result = reconstruct("p1", conn)
    assert len(result["recent_path"]) == 2
    assert result["recent_path"][0]["action"] == "implement"
    assert result["recent_path"][0]["to"] == b
    assert result["recent_path"][1]["action"] == "research"
    assert result["recent_path"][1]["to"] == c


def test_reconstruct_frontier(conn: sqlite3.Connection) -> None:
    a = _make_node(conn, project="p1", label="root")
    b = _make_node(conn, project="p1", label="pending task")
    c = _make_node(conn, project="p1", label="done task")
    _edge(conn, a, b)
    _edge(conn, a, c)
    conn.execute("UPDATE nodes SET status = 'done', activation = 0.8 WHERE id = ?", (c,))
    conn.execute("UPDATE nodes SET status = 'pending', activation = 0.6 WHERE id = ?", (b,))

    result = reconstruct("p1", conn, config={"activation_threshold": 0.5})
    assert len(result["frontier"]) == 1
    assert result["frontier"][0]["id"] == b


def test_reconstruct_blockers(conn: sqlite3.Connection) -> None:
    a = _make_node(conn, project="p1", label="root")
    b = _make_node(conn, project="p1", label="blocked item")
    conn.execute("UPDATE nodes SET status = 'blocked' WHERE id = ?", (b,))

    result = reconstruct("p1", conn)
    assert len(result["blockers"]) == 1
    assert result["blockers"][0]["id"] == b


def test_reconstruct_next_target(conn: sqlite3.Connection) -> None:
    a = _make_node(conn, project="p1", label="root")
    b = _make_node(conn, project="p1", label="do this next")
    c = _make_node(conn, project="p1", label="skip this")
    _edge(conn, a, b)
    _edge(conn, a, c)
    conn.execute("UPDATE nodes SET status = 'done', activation = 0.9 WHERE id = ?", (c,))
    conn.execute("UPDATE nodes SET status = 'pending', activation = 0.7 WHERE id = ?", (b,))

    result = reconstruct("p1", conn, cursor=a)
    assert result["next_target"]["id"] == b
    assert result["next_target"]["label"] == "do this next"


def test_compare_paths_basic(conn: sqlite3.Connection) -> None:
    a = _make_node(conn, project="p1", label="start")
    b = _make_node(conn, project="p1", label="cheap target")
    c = _make_node(conn, project="p1", label="expensive target")
    _edge(conn, a, b, "implement", prob=0.9, cost_tokens=5000)
    _edge(conn, a, c, "implement", prob=0.8, cost_tokens=15000)

    from openplan.core.read import compare_paths
    result = compare_paths("p1", conn, [c, b], cursor=a)
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["results"][0]["target"] == b
    assert result["results"][0]["cost_tokens"] <= result["results"][1]["cost_tokens"]


def test_optimize_basic(conn: sqlite3.Connection) -> None:
    a = _make_node(conn, project="p1", label="root")
    b = _make_node(conn, project="p1", label="step 1")
    c = _make_node(conn, project="p1", label="step 2")
    _edge(conn, a, b, "implement", cost_tokens=5000)
    _edge(conn, b, c, "implement", cost_tokens=3000)

    from openplan.core.read import optimize
    result = optimize("p1", conn, cursor=a)
    assert result["ok"] is True
    assert result["count"] == 2
    assert len(result["optimal_order"]) == 2
    assert result["total_cost"] > 0


def test_optimize_all_done(conn: sqlite3.Connection) -> None:
    a = _make_node(conn, project="p1", label="root")
    conn.execute("UPDATE nodes SET status = 'done' WHERE id = ?", (a,))

    from openplan.core.read import optimize
    result = optimize("p1", conn, cursor=a)
    assert result["count"] == 0
    assert result["optimal_order"] == []


def test_optimize_skips_done_blocked(conn: sqlite3.Connection) -> None:
    a = _make_node(conn, project="p1", label="root")
    b = _make_node(conn, project="p1", label="active")
    c = _make_node(conn, project="p1", label="blocked")
    d = _make_node(conn, project="p1", label="done")
    _edge(conn, a, b)
    _edge(conn, a, c)
    _edge(conn, a, d)
    conn.execute("UPDATE nodes SET status = 'blocked' WHERE id = ?", (c,))
    conn.execute("UPDATE nodes SET status = 'done' WHERE id = ?", (d,))

    from openplan.core.read import optimize
    result = optimize("p1", conn, cursor=a)
    assert result["count"] == 1
    assert result["optimal_order"][0]["id"] == b
