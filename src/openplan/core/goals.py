from __future__ import annotations

import re
import sqlite3
from typing import Any


def _parse_goal_markers(goal: str) -> list[str]:
    paren_re = re.compile(r'\([^)]*\)')
    processed = paren_re.sub(lambda m: m.group(0).replace(",", " and"), goal)
    parts = re.split(r'[,;.]+', processed)
    markers: list[str] = []
    for p in parts:
        p = p.strip().lower()
        if not p:
            continue
        for prefix in ("that ", "which ", "a ", "an ", "the ", "and "):
            if p.startswith(prefix):
                p = p[len(prefix):]
                break
        if p and len(p) > 3:
            markers.append(p)
    return markers


def _insert_goal_markers(project: str, goal: str, conn: sqlite3.Connection) -> None:
    markers = _parse_goal_markers(goal)
    for criterion in markers:
        conn.execute(
            "INSERT OR IGNORE INTO goal_markers (project, criterion) VALUES (?, ?)",
            (project, criterion),
        )
