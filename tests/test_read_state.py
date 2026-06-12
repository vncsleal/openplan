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


def _edge(conn: sqlite3.Connection, source: str, target: str, action: str = "transition") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO edges (source_id, target_id, action, prob) VALUES (?, ?, ?, 0.8)",
        (source, target, action),
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
    assert len(result["states"]) == 2
    assert len(result["edges"]) == 1
    assert result["statistics"]["total_states"] == 2
    assert result["statistics"]["total_edges"] == 1


def test_reconstruct_status_counts(conn: sqlite3.Connection) -> None:
    a = _make_node(conn, project="p1")
    b = _make_node(conn, project="p1")
    c = _make_node(conn, project="p1")
    conn.execute("UPDATE nodes SET status = 'done' WHERE id = ?", (b,))
    conn.execute("UPDATE nodes SET status = 'done' WHERE id = ?", (c,))

    result = reconstruct("p1", conn)
    assert result["statistics"]["status_counts"]["pending"] == 1
    assert result["statistics"]["status_counts"]["done"] == 2


def test_reconstruct_empty_project(conn: sqlite3.Connection) -> None:
    result = reconstruct("empty", conn)
    assert result["ok"] is True
    assert result["states"] == []
    assert result["statistics"]["total_states"] == 0


def test_reconstruct_with_reasoning(conn: sqlite3.Connection) -> None:
    props = json.dumps({"type": "decision", "question": "reconstruct?", "visit_count": 1})
    sid = _make_node(conn, project="p1", props=props)
    result = reconstruct("p1", conn)
    state = result["states"][0]
    assert state["reasoning"]["type"] == "decision"
    assert state["reasoning"]["question"] == "reconstruct?"
    assert state["raw_props"]["visit_count"] == 1
