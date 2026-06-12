from __future__ import annotations

import logging
import sqlite3
from typing import Any

_log = logging.getLogger("openplan.resolve")


def resolve_target(
    text: str,
    project: str,
    conn: sqlite3.Connection,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    tier = "none"
    results: list[dict[str, Any]] = []

    try:
        from openplan.core.embedding import get_cache, get_provider
        provider = get_provider()
        if provider.loaded:
            cache = get_cache()
            emb_results = cache.query(text, conn, top_k=top_k)
            if emb_results:
                for r in emb_results:
                    r["method"] = "embedding"
                results = emb_results
                tier = "embedding"
    except ImportError:
        pass

    if not results:
        import re as _re
        query_tokens = _re.findall(r"[^\s\[\]\(\)]+", text.strip())
        safe_tokens = [t for t in query_tokens if t and not _re.search(r'[^a-zA-Z0-9_α-ωΑ-Ω]', t)]
        fts_query = " OR ".join(f'"{t}"' for t in safe_tokens if t)
        if fts_query:
            try:
                rows = conn.execute(
                    "SELECT n.id, n.label, n.activation, n.frontier, n.status FROM nodes_fts f "
                    "JOIN nodes n ON n.rowid = f.rowid "
                    "WHERE n.project = ? AND nodes_fts MATCH ? ORDER BY rank LIMIT ?",
                    (project, fts_query, top_k),
                ).fetchall()
                if rows:
                    results = [dict(r) | {"method": "fts5", "similarity": 0.0} for r in rows]
                    tier = "fts5"
            except sqlite3.OperationalError:
                pass

    if not results:
        like = f"%{text}%"
        rows = conn.execute(
            "SELECT id, label, activation, frontier, status FROM nodes "
            "WHERE project = ? AND (label LIKE ? OR id LIKE ?) "
            "ORDER BY activation DESC LIMIT ?",
            (project, like, like, top_k),
        ).fetchall()
        if rows:
            results = [dict(r) | {"method": "like", "similarity": 0.0} for r in rows]
            tier = "like"

    _log.info("resolve_target(%r) -> tier=%s, count=%d", text, tier, len(results))
    return results


def resolve_goal_aligned(
    goal: str,
    project: str,
    conn: sqlite3.Connection,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    results = resolve_target(goal, project, conn, top_k=top_k)
    filtered = [r for r in results if r.get("status", "pending") not in ("done", "superseded")]
    return filtered
