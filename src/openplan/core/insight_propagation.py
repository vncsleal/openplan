from __future__ import annotations

import json
import sqlite3
from typing import Any


def propagate(conn: sqlite3.Connection, config: dict[str, Any]) -> int:
    inserted = 0
    try:
        from openplan.core.embedding import get_cache, get_provider
        provider = get_provider()
        if not provider.loaded:
            return 0
        cache = get_cache()
        cache.refresh(conn)
    except Exception:
        return 0

    threshold = config.get("insight_similarity_threshold", 0.7)
    recent = conn.execute(
        "SELECT payload, node_id FROM events WHERE event_type = 'calibrated' ORDER BY created_at DESC LIMIT 20"
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
        src = conn.execute("SELECT project FROM nodes WHERE id = ?", (source_state,)).fetchone()
        if not src:
            continue

        similar = cache.query(insight, conn, top_k=5)
        for s in similar:
            if s["similarity"] < threshold:
                continue
            tgt = conn.execute("SELECT project FROM nodes WHERE id = ?", (s["id"],)).fetchone()
            if not tgt or tgt["project"] == src["project"]:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO cross_project_insights (source_project, source_state, target_project, target_state, insight_text, similarity) VALUES (?, ?, ?, ?, ?, ?)",
                (src["project"], source_state, tgt["project"], s["id"], insight, s["similarity"]),
            )
            inserted += 1

    if inserted:
        conn.commit()
    return inserted
