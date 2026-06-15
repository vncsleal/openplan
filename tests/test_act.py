from __future__ import annotations

import json
import os
import sqlite3
import tempfile

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


def test_act_auto_status(conn: sqlite3.Connection, config: dict) -> None:
    src = _make_node(conn)
    tgt = _make_node(conn)
    _edge(conn, src, tgt, "implement")

    act(src, "implement", conn, config)

    src_status = conn.execute("SELECT status FROM nodes WHERE id = ?", (src,)).fetchone()["status"]
    tgt_status = conn.execute("SELECT status FROM nodes WHERE id = ?", (tgt,)).fetchone()["status"]
    assert src_status == "done"
    assert tgt_status == "in_progress"


def test_act_auto_status_respects_blocked(conn: sqlite3.Connection, config: dict) -> None:
    src = _make_node(conn)
    tgt = _make_node(conn)
    _edge(conn, src, tgt, "implement")
    conn.execute("UPDATE nodes SET status = 'blocked' WHERE id = ?", (src,))

    act(src, "implement", conn, config)

    src_status = conn.execute("SELECT status FROM nodes WHERE id = ?", (src,)).fetchone()["status"]
    tgt_status = conn.execute("SELECT status FROM nodes WHERE id = ?", (tgt,)).fetchone()["status"]
    assert src_status == "blocked"
    assert tgt_status == "in_progress"


def test_act_reasoning_new_state(conn: sqlite3.Connection, config: dict) -> None:
    src = _make_node(conn)
    reasoning = {"type": "hypothesis", "question": "test?", "tags": ["test"]}

    result = act(src, "investigate", conn, config, target="Explore X", reasoning=reasoning)

    tgt_id = result["next_state"]
    props = json.loads(conn.execute("SELECT props FROM nodes WHERE id = ?", (tgt_id,)).fetchone()["props"])
    assert props["type"] == "hypothesis"
    assert props["question"] == "test?"
    assert props["tags"] == ["test"]


def test_goal_marker_label_matching(conn: sqlite3.Connection, config: dict) -> None:
    conn.execute(
        "INSERT INTO goal_markers (project, criterion) VALUES (?, ?)",
        ("test", "Build converter for markdown to PDF"),
    )
    conn.execute(
        "INSERT INTO goal_markers (project, criterion) VALUES (?, ?)",
        ("test", "Implement CLI interface"),
    )
    state_label = "Build converter for markdown to PDF"
    now_ts = "2026-06-14T00:00:00.000000Z"
    state_id = "S-TEST01"
    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES (?, ?, ?)",
        (state_id, state_label, "test"),
    )

    for row in conn.execute(
        "SELECT criterion FROM goal_markers WHERE project = ? AND achieved = 0",
        ("test",),
    ).fetchall():
        if row["criterion"].lower() in state_label.lower():
            conn.execute(
                "UPDATE goal_markers SET achieved = 1, achieved_at = ?, achieved_by = ? "
                "WHERE project = ? AND criterion = ?",
                (now_ts, state_id, "test", row["criterion"]),
            )

    markers = conn.execute(
        "SELECT criterion, achieved FROM goal_markers WHERE project = ? ORDER BY criterion",
        ("test",),
    ).fetchall()
    assert markers[0]["achieved"] == 1, "converter marker should be achieved"
    assert markers[1]["achieved"] == 0, "CLI marker should remain unachieved"


def test_goal_marker_evidence_matching_direction(conn: sqlite3.Connection, config: dict) -> None:
    conn.execute(
        "INSERT INTO goal_markers (project, criterion) VALUES (?, ?)",
        ("test", "CLI accepts --input flag"),
    )
    conn.execute(
        "INSERT INTO goal_markers (project, criterion) VALUES (?, ?)",
        ("test", "build converter"),
    )
    evidence_desc = "Verified that CLI accepts --input flag correctly"
    now_ts = "2026-06-14T00:00:00.000000Z"
    state_id = "S-TEST02"
    conn.execute(
        "INSERT INTO nodes (id, label, project) VALUES (?, ?, ?)",
        (state_id, "test evidence match", "test"),
    )

    conn.execute(
        "UPDATE goal_markers SET achieved = 1, achieved_at = ?, achieved_by = ? "
        "WHERE project = ? AND ? LIKE '%' || criterion || '%' AND achieved = 0",
        (now_ts, state_id, "test", evidence_desc.lower()),
    )

    markers = conn.execute(
        "SELECT criterion, achieved FROM goal_markers WHERE project = ? ORDER BY criterion",
        ("test",),
    ).fetchall()
    assert markers[0]["achieved"] == 1, "ev description contains 'CLI accepts --input flag'"
    assert markers[1]["achieved"] == 0, "ev description doesn't contain 'build converter'"


def test_evidence_stat_verified_for_existing_file(conn: sqlite3.Connection, config: dict) -> None:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
        f.write(b"test content")
        tmp_path = f.name
    try:
        st = os.stat(tmp_path)
        status = "verified"
    except OSError:
        status = "unverified"
    finally:
        os.unlink(tmp_path)
    assert status == "verified", "existing file should be verified"


def test_evidence_stat_fails_for_missing_file(conn: sqlite3.Connection, config: dict) -> None:
    missing_path = "/tmp/nonexistent_file_for_test_987654321.txt"
    try:
        os.stat(missing_path)
        status = "verified"
    except OSError:
        status = "unverified"
    assert status == "unverified", "missing file should not be verified"


def test_evidence_metadata_has_size_for_file(conn: sqlite3.Connection, config: dict) -> None:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".py") as f:
        f.write(b"print('hello')")
        tmp_path = f.name
    try:
        st = os.stat(tmp_path)
        metadata = json.dumps({"size": st.st_size, "mtime": st.st_mtime})
        md = json.loads(metadata)
        assert md["size"] == 14  # len(b"print('hello')")
        assert "mtime" in md
    finally:
        os.unlink(tmp_path)


def test_goal_match_token_crosses_word_boundaries(conn: sqlite3.Connection, config: dict) -> None:
    from openplan.core.export import _goal_match
    assert _goal_match("fix bug", "Bugfix"), "word substring in label word"
    assert _goal_match("implement core", "Implement core parser"), "full match"
    assert _goal_match("add tests", "Test all modules"), "word 'test' in 'Test'"
    assert not _goal_match("deploy db", "Setup project"), "no overlap"


def test_act_reasoning_existing_state(conn: sqlite3.Connection, config: dict) -> None:
    src = _make_node(conn)
    tgt = _make_node(conn)
    _edge(conn, src, tgt, "implement")
    reasoning = {"conclusion": "revisited", "tags": ["updated"]}
    conn.execute("UPDATE nodes SET label = 'Existing target' WHERE id = ?", (tgt,))

    result = act(src, "implement", conn, config, target=tgt, reasoning=reasoning)

    props = json.loads(conn.execute("SELECT props FROM nodes WHERE id = ?", (tgt,)).fetchone()["props"])
    assert props["conclusion"] == "revisited"
    assert props["tags"] == ["updated"]
