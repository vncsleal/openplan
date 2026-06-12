from __future__ import annotations

import sqlite3
import threading

import pytest

from openplan.core.maintenance import _run_cycle, start_background_maintenance
from openplan.db.schema import init_db


@pytest.fixture
def conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def config() -> dict:
    return {"maintenance_interval_minutes": 60}


@pytest.fixture
def write_lock() -> threading.Lock:
    return threading.Lock()


def _make_node(conn: sqlite3.Connection, project: str) -> str:
    row = conn.execute(
        "SELECT COALESCE(MAX(CAST(SUBSTR(id, 3) AS INTEGER)), 0) + 1 AS next FROM nodes"
    ).fetchone()
    sid = f"S-{row['next']:06d}"
    conn.execute("INSERT INTO nodes (id, label, project) VALUES (?, ?, ?)", (sid, "", project))
    return sid


def _edge(conn: sqlite3.Connection, source: str, target: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO edges (source_id, target_id, action, cost_tokens, cost_risk, prob) VALUES (?, ?, 'implement', 10000, 0.1, 0.8)",
        (source, target),
    )


def test_run_cycle_no_projects(conn: sqlite3.Connection, config: dict, write_lock: threading.Lock) -> None:
    notifs = _run_cycle(conn, config, write_lock)
    assert notifs == []


def test_run_cycle_lock_timeout(conn: sqlite3.Connection, config: dict) -> None:
    lock = threading.Lock()
    lock.acquire()
    notifs = _run_cycle(conn, config, lock)
    assert len(notifs) == 1
    assert notifs[0]["code"] == "MAINTENANCE_SKIPPED"
    lock.release()


def test_run_cycle_with_project(conn: sqlite3.Connection, config: dict, write_lock: threading.Lock) -> None:
    root = _make_node(conn, "proj-a")
    child = _make_node(conn, "proj-a")
    _edge(conn, root, child)
    conn.commit()
    notifs = _run_cycle(conn, config, write_lock)
    assert isinstance(notifs, list)


def test_run_cycle_auto_fix_orphans(conn: sqlite3.Connection, config: dict, write_lock: threading.Lock) -> None:
    _make_node(conn, "proj-a")
    for _ in range(5):
        _make_node(conn, "proj-a")
    conn.commit()
    notifs = _run_cycle(conn, config, write_lock)
    fix_notifs = [n for n in notifs if n["code"] == "AUTO_FIX"]
    assert len(fix_notifs) > 0
    edges_after = conn.execute("SELECT COUNT(*) AS cnt FROM edges").fetchone()["cnt"]
    assert edges_after > 0


def test_run_cycle_with_compress(conn: sqlite3.Connection, config: dict, write_lock: threading.Lock) -> None:
    root = _make_node(conn, "proj-a")
    child = _make_node(conn, "proj-a")
    _edge(conn, root, child)
    for i in range(110):
        conn.execute(
            "INSERT INTO events (id, project, node_id, event_type, payload) VALUES (?, 'proj-a', ?, 'calibrated', '{}')",
            (f"E-{i+1:06d}", root),
        )
    conn.commit()
    notifs = _run_cycle(conn, config, write_lock)
    assert isinstance(notifs, list)


def test_run_cycle_project_limit(conn: sqlite3.Connection, config: dict, write_lock: threading.Lock) -> None:
    for p in ("proj-a", "proj-b", "proj-c"):
        root = _make_node(conn, p)
        _edge(conn, root, _make_node(conn, p))
    conn.commit()
    notifs = _run_cycle(conn, config, write_lock, project_limit=2)
    assert isinstance(notifs, list)


def test_start_background_maintenance(conn: sqlite3.Connection, config: dict, write_lock: threading.Lock) -> None:
    queue: list[dict] = []
    thread = start_background_maintenance(conn, config, write_lock, queue)
    assert isinstance(thread, threading.Thread)
    assert thread.daemon
    assert thread.is_alive()
