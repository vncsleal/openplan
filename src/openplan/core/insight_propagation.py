from __future__ import annotations

import json
import sqlite3
from typing import Any


def propagate(conn: sqlite3.Connection, config: dict[str, Any]) -> int:
    inserted = 0
    threshold = config.get("insight_similarity_threshold", 0.7)

    recent = conn.execute(
        "SELECT payload, node_id, project FROM events WHERE event_type = 'calibrated' ORDER BY created_at DESC LIMIT 20"
    ).fetchall()

    for ev in recent:
        try:
            payload = json.loads(ev["payload"])
        except (json.JSONDecodeError, TypeError):
            continue
        insight = payload.get("insight", "")
        if not insight:
            continue
        source_state = ev["node_id"]
        source_project = ev["project"]

        # Try embedding similarity first
        matched = _embedding_match(conn, insight, source_project, threshold)

        # FTS5 fallback
        if not matched:
            matched = _fts5_match(conn, insight, source_project)

        # LIKE fallback
        if not matched:
            matched = _like_match(conn, insight, source_project)

        for target_project, target_state, sim in matched:
            conn.execute(
                "INSERT OR IGNORE INTO cross_project_insights (source_project, source_state, target_project, target_state, insight_text, similarity) VALUES (?, ?, ?, ?, ?, ?)",
                (source_project, source_state, target_project, target_state, insight, sim),
            )
            inserted += 1

    if inserted:
        conn.commit()
    return inserted


def _embedding_match(conn: sqlite3.Connection, insight: str, source_project: str, threshold: float) -> list[tuple[str, str, float]]:
    try:
        from openplan.core.embedding import get_cache, get_provider
        provider = get_provider()
        if not provider.loaded:
            return []
        cache = get_cache()
        cache.refresh(conn)
        similar = cache.query(insight, conn, top_k=5)
        results = []
        for s in similar:
            if s["similarity"] < threshold:
                continue
            tgt = conn.execute("SELECT project FROM nodes WHERE id = ?", (s["id"],)).fetchone()
            if not tgt or tgt["project"] == source_project:
                continue
            results.append((tgt["project"], s["id"], s["similarity"]))
        return results
    except Exception:
        return []


def _fts5_match(conn: sqlite3.Connection, insight: str, source_project: str) -> list[tuple[str, str, float]]:
    try:
        words = [w for w in insight.lower().split() if len(w) > 3][:5]
        if not words:
            return []
        query = " OR ".join(words)
        rows = conn.execute(
            "SELECT n.id, n.project FROM nodes_fts f JOIN nodes n ON n.rowid = f.rowid "
            "WHERE nodes_fts MATCH ? AND n.project != ? ORDER BY rank LIMIT 5",
            (query, source_project),
        ).fetchall()
        return [(r["project"], r["id"], 0.6) for r in rows]
    except Exception:
        return []


def _like_match(conn: sqlite3.Connection, insight: str, source_project: str) -> list[tuple[str, str, float]]:
    words = [w for w in insight.lower().split() if len(w) > 3][:3]
    if not words:
        return []
    like_q = f"%{words[0]}%"
    rows = conn.execute(
        "SELECT id, project FROM nodes WHERE label LIKE ? AND project != ? LIMIT 5",
        (like_q, source_project),
    ).fetchall()
    return [(r["project"], r["id"], 0.4) for r in rows]
