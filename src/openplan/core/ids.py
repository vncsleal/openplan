from __future__ import annotations

import sqlite3

from openplan.core.utils import now as _now


def generate_id(project: str, conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT id FROM nodes ORDER BY id DESC LIMIT 1").fetchone()
    next_num = (int(row["id"][2:]) if row else 0) + 1
    return f"S-{next_num:06d}"


def generate_branch_id(project: str, conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(id, 3) AS INTEGER)) AS max_id FROM events WHERE event_type = 'branched'"
    ).fetchone()
    next_num = (row["max_id"] or 0) + 1
    return f"B-{next_num:06d}"


def generate_event_id(project: str, conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(id, 3) AS INTEGER)) AS max_id FROM events"
    ).fetchone()
    next_num = (row["max_id"] or 0) + 1
    return f"E-{next_num:06d}"


def _ensure_node(project: str, label: str, conn: sqlite3.Connection, parent_id: str | None = None) -> str:
    sid = generate_id(project, conn)
    now = _now()
    conn.execute(
        "INSERT INTO nodes (id, label, project, parent_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (sid, label, project, parent_id, now, now),
    )
    return sid


def _increment_visit(state_id: str, conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE nodes SET props = json_set(props, '$.visit_count', "
        "COALESCE(json_extract(props, '$.visit_count'), 0) + 1) WHERE id = ?",
        (state_id,),
    )
