from __future__ import annotations

import sqlite3


def _detect_cycle(conn: sqlite3.Connection, source_id: str, target_id: str, action: str) -> bool:
    row = conn.execute(
        """
        WITH RECURSIVE descendants(id) AS (
            SELECT target_id FROM edges WHERE source_id = ?
            UNION ALL
            SELECT e.target_id FROM edges e JOIN descendants d ON e.source_id = d.id
        )
        SELECT 1 FROM descendants WHERE id = ?
        """,
        (target_id, source_id),
    ).fetchone()
    return row is not None


def _prune_stale_branches(source_id: str, conn: sqlite3.Connection, session_id: str = "", rate_limit: int = 5, stale_hours: float = 24.0) -> None:
    import logging
    _log = logging.getLogger("openplan.state")
    from openplan.core.utils import now as _now
    stale = conn.execute(
        "SELECT id FROM nodes WHERE project = (SELECT project FROM nodes WHERE id = ?) "
        "AND status = 'pending' AND id != ? AND id NOT IN (SELECT source_id FROM edges) "
        "AND (julianday(?) - julianday(created_at)) * 24 >= ? LIMIT ?",
        (source_id, source_id, _now(), stale_hours, rate_limit),
    ).fetchall()
    for r in stale:
        conn.execute("UPDATE nodes SET status = 'superseded' WHERE id = ?", (r["id"],))
        if _log.isEnabledFor(logging.DEBUG):
            _log.debug("Pruned stale branch: %s", r["id"])


def _nearest_active_ancestor(state_id: str, conn: sqlite3.Connection) -> str:
    project: str | None = None
    visited: set[str] = set()
    stack = [state_id]
    while stack:
        nid = stack.pop()
        if nid in visited:
            continue
        visited.add(nid)
        row = conn.execute("SELECT status, project FROM nodes WHERE id = ?", (nid,)).fetchone()
        if row:
            project = row["project"]
            if row["status"] not in ("superseded", "cascade_blocked"):
                return nid
        edges = conn.execute("SELECT source_id FROM edges WHERE target_id = ?", (nid,)).fetchall()
        for e in edges:
            stack.append(e["source_id"])
    if project:
        root = conn.execute(
            "SELECT id FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
            (project,),
        ).fetchone()
        if root:
            return root["id"]
    return state_id
