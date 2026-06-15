from __future__ import annotations

import json
import sqlite3
from typing import Any

from openplan.core.errors import PreconditionError


def _check_preconditions(edge: dict, conn: sqlite3.Connection) -> None:
    raw = edge.get("conditions", "")
    if not raw:
        return
    try:
        conditions = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(conditions, list):
        return
    for cond in conditions:
        if isinstance(cond, dict):
            field = cond.get("field", "")
            expected = cond.get("value")
            source_id = edge["source_id"]
            row = conn.execute("SELECT props FROM nodes WHERE id = ?", (source_id,)).fetchone()
            if row:
                try:
                    props = json.loads(row["props"]) if isinstance(row["props"], str) else row["props"]
                except (json.JSONDecodeError, TypeError):
                    props = {}
                actual = props.get(field)
                if actual != expected:
                    raise PreconditionError(source_id, edge.get("action", ""), f"{field}={expected}")


def _check_postconditions(postconditions: dict, target_id: str, conn: sqlite3.Connection) -> None:
    if not postconditions:
        return
    row = conn.execute("SELECT props FROM nodes WHERE id = ?", (target_id,)).fetchone()
    if not row:
        return
    try:
        current_props = json.loads(row["props"]) if isinstance(row["props"], str) else row["props"]
    except (json.JSONDecodeError, TypeError):
        current_props = {}
    for key, expected_value in postconditions.items():
        actual_value = current_props.get(key)
        if actual_value is not None and actual_value != expected_value:
            raise PreconditionError(target_id, "postcondition", f"{key}={expected_value} (actual={actual_value})")
