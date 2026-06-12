from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger("openplan.rlhf")


def _auth_header(password: str | None = None) -> dict[str, str]:
    pwd = password or os.environ.get("OPENCODE_SERVER_PASSWORD", "")
    if pwd:
        token = base64.b64encode(f"opencode:{pwd}".encode()).decode()
        return {"Authorization": f"Basic {token}", "Accept": "application/json"}
    return {"Accept": "application/json"}


def fetch_opencode_session(
    session_id: str,
    base_url: str = "http://localhost:4096",
    endpoint: str = "/session/{id}/message",
    password: str | None = None,
) -> dict[str, Any] | None:
    url = f"{base_url.rstrip('/')}{endpoint.format(id=session_id)}"
    try:
        req = urllib.request.Request(url, headers=_auth_header(password))
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, ConnectionResetError, TimeoutError) as e:
        _log.warning("Failed to fetch opencode session %s: %s", session_id, e)
        return None
    except json.JSONDecodeError as e:
        _log.warning("Non-JSON response for session %s: %s", session_id, e)
        return None


def _parse_ts(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc)


def correlate_events(
    session_id: str,
    events: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    window_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    correlated: list[dict[str, Any]] = []
    for msg in messages:
        msg_ts = _parse_ts(msg.get("created_at") or msg.get("timestamp") or "")
        nearby: list[dict[str, Any]] = []
        for evt in events:
            evt_ts = _parse_ts(evt.get("created_at", ""))
            diff = abs((evt_ts - msg_ts).total_seconds())
            if diff <= window_seconds:
                nearby.append({
                    "event_id": evt.get("id"),
                    "event_type": evt.get("event_type"),
                    "project": evt.get("project"),
                    "node_id": evt.get("node_id"),
                    "payload": json.loads(evt.get("payload", "{}")) if isinstance(evt.get("payload"), str) else evt.get("payload", {}),
                    "delta_seconds": round(diff, 3),
                })
        correlated.append({
            "session_id": session_id,
            "message": msg,
            "correlated_events": sorted(nearby, key=lambda x: x["delta_seconds"]),
            "event_count": len(nearby),
        })
    return correlated


def build_rlhf_dataset(
    db_path: str,
    opencode_base_url: str = "http://localhost:4096",
    opencode_endpoint: str = "/session/{id}/message",
    window_seconds: float = 30.0,
    max_sessions: int = 0,
    opencode_password: str | None = None,
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        session_rows = conn.execute(
            "SELECT DISTINCT session_id, project FROM events "
            "WHERE session_id IS NOT NULL AND session_id != '' "
            "ORDER BY session_id"
        ).fetchall()
    finally:
        conn.close()

    seen: set[str] = set()
    dataset: list[dict[str, Any]] = []
    for row in session_rows:
        sid = row["session_id"]
        if sid in seen:
            continue
        seen.add(sid)
        if max_sessions and len(dataset) >= max_sessions:
            break

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            event_rows = conn.execute(
                "SELECT id, project, node_id, event_type, payload, created_at "
                "FROM events WHERE session_id = ? ORDER BY created_at",
                (sid,),
            ).fetchall()
        finally:
            conn.close()

        events = [dict(r) for r in event_rows]
        messages_raw = fetch_opencode_session(sid, opencode_base_url, opencode_endpoint, password=opencode_password)
        if messages_raw is None:
            _log.info("Skipping session %s — opencode server unreachable or no data", sid)
            continue

        messages_list: list[dict[str, Any]] = []
        if isinstance(messages_raw, list):
            messages_list = messages_raw
        elif isinstance(messages_raw, dict):
            for key in ("messages", "data", "conversation"):
                val = messages_raw.get(key)
                if isinstance(val, list):
                    messages_list = val
                    break
            if not messages_list:
                messages_list = [messages_raw]

        correlated = correlate_events(sid, events, messages_list, window_seconds)
        dataset.append({
            "session_id": sid,
            "project": row["project"],
            "event_count": len(events),
            "message_count": len(messages_list),
            "correlated_pairs": correlated,
        })

    return dataset
