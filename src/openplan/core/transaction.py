from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from typing import Any

from openplan.core.ids import generate_event_id
from openplan.core.utils import now as _now

_log = logging.getLogger("openplan.state")


def _idempotency_key(node_id: str, event_type: str, action: str = "") -> str:
    raw = f"{node_id}:{event_type}:{action}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _record_event(conn: sqlite3.Connection, node_id: str, project: str, event_type: str, payload: dict, session_id: str = "") -> str:
    eid = generate_event_id(project, conn)
    ikey = _idempotency_key(node_id, event_type, payload.get("action", ""))
    now = _now()
    conn.execute(
        "INSERT OR IGNORE INTO events (id, project, node_id, event_type, payload, version, idempotency_key, session_id, created_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
        (eid, project, node_id, event_type, json.dumps(payload), ikey, session_id, now),
    )
    return eid


def _safe_savepoint(conn: sqlite3.Connection, name: str) -> bool:
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
        _log.warning("Invalid savepoint name: %s", name)
        return False
    try:
        conn.execute(f'SAVEPOINT "{name}"')
        return True
    except sqlite3.OperationalError as e:
        _log.warning("Savepoint %s failed: %s", name, e)
        return False


def _safe_release(conn: sqlite3.Connection, name: str, owned: bool) -> None:
    if owned:
        conn.execute(f'RELEASE SAVEPOINT "{name}"')


def _safe_rollback(conn: sqlite3.Connection, name: str, owned: bool) -> None:
    if owned:
        try:
            conn.execute(f'ROLLBACK TO SAVEPOINT "{name}"')
        except sqlite3.OperationalError:
            pass
