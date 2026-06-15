from __future__ import annotations

import re
import sqlite3
from typing import Any


_FORMAT_PATTERN = re.compile(r'\b(jpeg|png|webp|gif|svg|bmp|tiff|json|yaml|yml|toml|xml|csv|html|css|js|ts|jsx|tsx|md|pdf|doc|docx|xls|xlsx|zip|tar|gz|mp3|mp4|avi|mov|exif|iptc|xmp|rsync|esm|cjs|sqlite|postgres|mysql|redis|mongodb)\b', re.IGNORECASE)


def _is_format_list(phrase: str) -> bool:
    return bool(_FORMAT_PATTERN.search(phrase))


def _parse_goal_markers(goal: str) -> list[str]:
    paren_re = re.compile(r'\([^)]*\)')
    processed = paren_re.sub(lambda m: m.group(0).replace(",", " and"), goal)
    parts = re.split(r'[,;.]+', processed)
    markers: list[str] = []
    i = 0
    while i < len(parts):
        p = parts[i].strip().lower()
        if not p:
            i += 1
            continue
        # Merge consecutive parts that look like format lists: "supports jpeg, png, webp"
        merged = p
        while i + 1 < len(parts):
            next_p = parts[i + 1].strip()
            if not next_p:
                i += 1
                continue
            # If this or next part looks like a format/tool list, merge them
            if _is_format_list(merged) or _is_format_list(next_p):
                merged = merged + ", " + next_p.strip().lower()
                i += 1
            else:
                break
        for prefix in ("that ", "which ", "a ", "an ", "the ", "and "):
            if merged.startswith(prefix):
                merged = merged[len(prefix):]
                break
        if merged and len(merged) > 3:
            markers.append(merged)
        i += 1
    return markers


def _insert_goal_markers(project: str, goal: str, conn: sqlite3.Connection) -> None:
    markers = _parse_goal_markers(goal)
    for criterion in markers:
        conn.execute(
            "INSERT OR IGNORE INTO goal_markers (project, criterion) VALUES (?, ?)",
            (project, criterion),
        )
