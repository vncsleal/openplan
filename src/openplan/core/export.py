from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from openplan.core.activation import get_activation, recompute_all_dirty
from openplan.core.errors import OpenPlanError
from openplan.core.state import _now, _record_event, _safe_release, _safe_rollback, _safe_savepoint


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")


def export(project: str, conn: sqlite3.Connection, fmt: str = "json") -> dict[str, Any]:
    if fmt == "matrix":
        edges = conn.execute(
            "SELECT e.* FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ?",
            (project,),
        ).fetchall()
        sparse = [
            {"source": e["source_id"], "target": e["target_id"], "value": e["cost_tokens"] * (1 + e["cost_risk"])}
            for e in edges
        ]
        return {"format": "matrix", "sparse": sparse, "project": project, "exported_at": _now()}

    if fmt == "graphml":
        nodes = conn.execute("SELECT * FROM nodes WHERE project = ?", (project,)).fetchall()
        rows = conn.execute(
            "SELECT e.* FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ?",
            (project,),
        ).fetchall()
        parts: list[str] = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
            '  <key id="label" for="node" attr.name="label" attr.type="string"/>',
            '  <key id="activation" for="node" attr.name="activation" attr.type="double"/>',
            '  <key id="action" for="edge" attr.name="action" attr.type="string"/>',
            '  <key id="cost" for="edge" attr.name="cost" attr.type="double"/>',
            '  <key id="prob" for="edge" attr.name="probability" attr.type="double"/>',
            f'  <graph id="{project}" edgedefault="directed">',
        ]
        for r in nodes:
            parts.append(f'    <node id="{r["id"]}">')
            parts.append(f'      <data key="label">{_xml_escape(r["label"])}</data>')
            parts.append(f'      <data key="activation">{r["activation"]}</data>')
            parts.append("    </node>")
        for e in rows:
            parts.append(f'    <edge source="{e["source_id"]}" target="{e["target_id"]}">')
            parts.append(f'      <data key="action">{_xml_escape(e["action"])}</data>')
            parts.append(f'      <data key="cost">{e["cost_tokens"]}</data>')
            parts.append(f'      <data key="prob">{e["prob"]}</data>')
            parts.append("    </edge>")
        parts.append("  </graph>")
        parts.append("</graphml>")
        return {"format": "graphml", "graphml": "\n".join(parts), "project": project, "exported_at": _now()}

    nodes = [dict(r) for r in conn.execute("SELECT * FROM nodes WHERE project = ?", (project,)).fetchall()]
    edges = [dict(r) for r in conn.execute("SELECT e.* FROM edges e JOIN nodes n ON n.id = e.source_id WHERE n.project = ?", (project,)).fetchall()]
    events = [dict(r) for r in conn.execute("SELECT * FROM events WHERE project = ?", (project,)).fetchall()]
    return {"nodes": nodes, "edges": edges, "events": events, "project": project, "exported_at": _now(), "version": "0.1.0"}


def _project_root_node(project: str, conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT id FROM nodes WHERE project = ? ORDER BY created_at ASC LIMIT 1",
        (project,),
    ).fetchone()
    return row["id"] if row else None


def _get_archive_cols(conn: sqlite3.Connection) -> str:
    cols = conn.execute("PRAGMA table_info(events_archive)").fetchall()
    names = [r["name"] for r in cols]
    return ", ".join(names)


def compress(
    project: str, conn: sqlite3.Connection, config: dict[str, Any],
    older_than_days: int = 30, merge_orphans: bool = True, session_id: str = "",
) -> dict[str, Any]:
    owned = _safe_savepoint(conn, "compress_tx")
    try:
        now = _now()
        cutoff = datetime.now(timezone.utc).timestamp() - older_than_days * 86400
        cutoff_str = datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        archive_cols = _get_archive_cols(conn)
        conn.execute(
            f"INSERT OR IGNORE INTO events_archive ({archive_cols}) "
            f"SELECT {archive_cols} FROM events WHERE project = ? AND created_at < ?",
            (project, cutoff_str),
        )
        archived = conn.execute("SELECT changes() AS cnt").fetchone()["cnt"]
        conn.execute("DELETE FROM events WHERE project = ? AND created_at < ?", (project, cutoff_str))
        deleted = conn.execute("SELECT changes() AS cnt").fetchone()["cnt"]

        merged = 0
        if merge_orphans:
            recompute_all_dirty(conn, config)
            orphans = conn.execute(
                "SELECT n.id, n.label FROM nodes n "
                "WHERE n.project = ? AND n.activation < 0.3 "
                "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.source_id = n.id) "
                "AND n.id NOT IN (SELECT MIN(n2.id) FROM nodes n2 WHERE n2.project = ?)",
                (project, project),
            ).fetchall()
            if orphans:
                parent = conn.execute(
                    "SELECT id FROM nodes WHERE project = ? ORDER BY activation DESC LIMIT 1",
                    (project,),
                ).fetchone()
                if parent:
                    orphan_ids = [o["id"] for o in orphans]
                    placeholders = ",".join("?" * len(orphan_ids))
                    conn.execute(
                        "INSERT OR IGNORE INTO edges (source_id, target_id, action, prob, created_at, updated_at) "
                        f"SELECT ?, id, 'merged', 1.0, ?, ? FROM nodes WHERE id IN ({placeholders})",
                        (parent["id"], now, now, *orphan_ids),
                    )
                    merged = len(orphan_ids)

        event_node = _project_root_node(project, conn)
        if event_node:
            _record_event(conn, event_node, project, "compressed", {
                "archived_events": archived, "deleted_events": deleted, "merged_orphans": merged,
            }, session_id)

        _safe_release(conn, "compress_tx", owned)
    except Exception:
        _safe_rollback(conn, "compress_tx", owned)
        raise

    return {"ok": True, "archived_events": archived, "deleted_events": deleted, "merged_orphans": merged}


def project_list(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT n.project, n.id AS root_id, n.label, COUNT(DISTINCT e2.event_type) AS event_type_count, "
        "MAX(e.created_at) AS last_action "
        "FROM nodes n "
        "LEFT JOIN events e ON e.node_id = n.id AND e.event_type = 'acted' "
        "LEFT JOIN events e2 ON e2.project = n.project "
        "WHERE n.id IN (SELECT MIN(n2.id) FROM nodes n2 GROUP BY n2.project) "
        "GROUP BY n.project ORDER BY last_action DESC"
    ).fetchall()
    projects = {}
    for r in rows:
        projects[r["project"]] = {
            "root_id": r["root_id"], "label": r["label"],
            "event_types": r["event_type_count"], "last_action": r["last_action"],
        }
    return {"projects": list(projects.keys()), "roots": projects, "count": len(projects)}
