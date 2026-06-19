from __future__ import annotations

import os
import secrets
import time
from typing import Any

import httpx

from .db import get_key_usage

RATE_LIMITS: dict[str, int] = {
    "free": 100,
    "pro": 999999,
}

GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "openplan-cli")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")


def get_tier_from_api_key(conn: Any, api_key: str) -> str:
    if not api_key:
        return ""
    row = conn.execute(
        "SELECT tier FROM api_keys WHERE key = ? AND is_active = 1",
        (api_key,),
    ).fetchone()
    return row["tier"] if row else ""


def get_rate_limit_for_tier(tier: str) -> int:
    return RATE_LIMITS.get(tier, 100)


def generate_api_key(
    conn: Any, user_id: str = "", tier: str = "free", label: str = ""
) -> str:
    key = f"op_{secrets.token_hex(24)}"
    conn.execute(
        "INSERT OR IGNORE INTO api_keys (key, user_id, tier, label, is_active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
        (key, user_id, tier, label, time.time()),
    )
    conn.commit()
    return key


def revoke_api_key(conn: Any, api_key: str) -> bool:
    conn.execute("UPDATE api_keys SET is_active = 0 WHERE key = ?", (api_key,))
    conn.commit()
    return conn.execute("SELECT changes()").fetchone()[0] > 0


def get_user_by_github_id(conn: Any, github_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM users WHERE github_id = ?", (github_id,)
    ).fetchone()
    return dict(row) if row else None


def create_user(
    conn: Any, github_id: int, login: str, email: str, avatar_url: str
) -> str:
    user_id = f"u_{secrets.token_hex(12)}"
    conn.execute(
        "INSERT OR IGNORE INTO users (id, github_id, login, email, avatar_url, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, github_id, login, email, avatar_url, time.time()),
    )
    conn.commit()
    return user_id


def create_oauth_session(
    conn: Any, device_code: str, user_code: str, expires_in: int = 600
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO oauth_sessions (device_code, user_code, state, created_at, expires_at) VALUES (?, ?, 'pending', ?, ?)",
        (device_code, user_code, time.time(), time.time() + expires_in),
    )
    conn.commit()


def poll_oauth_session(conn: Any, device_code: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM oauth_sessions WHERE device_code = ?",
        (device_code,),
    ).fetchone()
    return dict(row) if row else None


def complete_oauth_session(
    conn: Any,
    device_code: str,
    github_token: str,
    access_token: str,
    refresh_token: str,
) -> None:
    conn.execute(
        "UPDATE oauth_sessions SET state = 'completed', github_token = ?, access_token = ?, refresh_token = ? WHERE device_code = ?",
        (github_token, access_token, refresh_token, device_code),
    )
    conn.commit()


async def start_github_device_flow() -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/device/code",
            data={
                "client_id": GITHUB_CLIENT_ID,
                "scope": "read:user user:email",
            },
            headers={"Accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()


async def poll_github_token(device_code: str) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": GITHUB_CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
            timeout=10,
        )
        return resp.json()


async def get_github_user(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "openplan",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()


def get_subscription(conn: Any, user_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM subscriptions WHERE user_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def create_subscription(
    conn: Any, stripe_sub_id: str, user_id: str, stripe_customer_id: str, tier: str
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO subscriptions (stripe_subscription_id, user_id, stripe_customer_id, tier, status, current_period_end, created_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
        (
            stripe_sub_id,
            user_id,
            stripe_customer_id,
            tier,
            time.time() + 30 * 86400,
            time.time(),
        ),
    )
    conn.commit()


def cancel_subscription(conn: Any, stripe_sub_id: str) -> None:
    conn.execute(
        "UPDATE subscriptions SET status = 'canceled' WHERE stripe_subscription_id = ?",
        (stripe_sub_id,),
    )
    conn.commit()
