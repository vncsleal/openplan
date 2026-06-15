from __future__ import annotations

from datetime import datetime, timezone


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
