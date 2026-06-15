from __future__ import annotations

import os
import secrets
import sqlite3
import time

RATE_LIMITS: dict[str, int] = {
    "free": 100,     # 100 events per minute
    "pro": 1000,     # 1000 events per minute
    "enterprise": 10000,
}


def get_tier_from_api_key(conn: sqlite3.Connection, api_key: str) -> str:
    if not api_key:
        return "free" if not os.environ.get("OPENPLAN_REQUIRE_API_KEY") else ""
    row = conn.execute(
        "SELECT tier FROM api_keys WHERE key = ? AND is_active = 1",
        (api_key,),
    ).fetchone()
    return row["tier"] if row else ""


def get_rate_limit_for_tier(tier: str) -> int:
    return RATE_LIMITS.get(tier, 100)


def generate_api_key(conn: sqlite3.Connection, tier: str = "free", label: str = "") -> str:
    key = f"op_{secrets.token_hex(24)}"
    conn.execute(
        "INSERT OR IGNORE INTO api_keys (key, tier, label, is_active, created_at) VALUES (?, ?, ?, 1, ?)",
        (key, tier, label, time.time()),
    )
    conn.commit()
    return key
