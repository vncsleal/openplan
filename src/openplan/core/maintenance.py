from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any

_log = logging.getLogger("openplan.maintenance")

from openplan.core.graph import _graph_health, diagnostics as _diagnostics
from openplan.core.export import compress as _compress


def _run_cycle(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    write_lock: threading.Lock,
    project_limit: int = 10,
) -> list[dict[str, Any]]:
    notifications: list[dict[str, Any]] = []
    if not write_lock.acquire(timeout=2.0):
        notifications.append({"severity": "warning", "code": "MAINTENANCE_SKIPPED", "message": "background maintenance skipped: database busy"})
        return notifications
    try:
        projects = [r["project"] for r in conn.execute(
            "SELECT project FROM nodes GROUP BY project ORDER BY MAX(created_at) DESC LIMIT ?",
            (project_limit,),
        ).fetchall()]
        for project in projects:
            try:
                diag = _diagnostics(project, conn, config=config, auto_fix=True)
                fixes = diag.get("fixes_applied", 0)
                if fixes:
                    notifications.append({"severity": "info", "code": "AUTO_FIX", "message": f"auto-fixed {fixes} issues in {project}", "project": project})
                for issue in diag.get("issues", [])[:2]:
                    if not issue.get("notify", True):
                        continue
                    notifications.append({"severity": issue.get("severity", "info"), "code": issue["code"], "message": issue["message"], "project": project})
                if diag.get("overview", {}).get("events", 0) > 100:
                    _compress(project, conn, config, older_than_days=30, merge_orphans=False)
            except Exception:
                _log.exception("Maintenance cycle failed for %s", project)
                continue
        conn.commit()
    finally:
        write_lock.release()
    return notifications


def start_background_maintenance(
    conn: sqlite3.Connection,
    config: dict[str, Any],
    write_lock: threading.Lock,
    notification_queue: list[dict[str, Any]],
) -> threading.Thread:
    interval = config.get("maintenance_interval_minutes", 5) * 60.0
    stop_event = threading.Event()

    def _loop() -> None:
        notifs = _run_cycle(conn, config, write_lock)
        notification_queue.extend(notifs)
        while not stop_event.is_set():
            if stop_event.wait(interval):
                break
            notifs = _run_cycle(conn, config, write_lock)
            notification_queue.extend(notifs)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread
