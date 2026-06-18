from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request

v1 = APIRouter(prefix="/v1")

from .auth import (
    get_tier_from_api_key, get_rate_limit_for_tier, generate_api_key,
    revoke_api_key, get_user_by_github_id, create_user,
    create_oauth_session, poll_oauth_session, complete_oauth_session,
    start_github_device_flow, poll_github_token, get_github_user,
    get_subscription, create_subscription, cancel_subscription,
    get_key_usage,
)
from .db import get_conn, init_db, insert_event, get_calibration, get_rate_limit, increment_rate_limit
from .models import TelemetryBatch, CalibrationResponse, Baseline, HealthResponse

_log = logging.getLogger("openplan.api")
VERSION = "0.1.0"

conn: Any = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global conn
    conn = get_conn()
    init_db(conn)
    _log.info("Telemetry API started")
    yield
    conn.close()


app = FastAPI(
    title="OpenPlan Telemetry API",
    version=VERSION,
    lifespan=lifespan,
)
app.include_router(v1)


def _get_tier(request: Request) -> str:
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not api_key:
        api_key = request.query_params.get("api_key", "")
    tier = get_tier_from_api_key(conn, api_key)
    if not tier and os.environ.get("OPENPLAN_REQUIRE_API_KEY"):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return tier or "free"


# ─── Health ──────────────────────────────────────────────────────────────────

@v1.get("/health")
async def health() -> HealthResponse:
    count = conn.execute("SELECT COUNT(*) AS cnt FROM calibration_events").fetchone()
    return HealthResponse(
        ok=True,
        events_count=count["cnt"] if count else 0,
        version=VERSION,
    )


# ─── Telemetry ───────────────────────────────────────────────────────────────

@v1.post("/checkpoints")
async def post_telemetry(batch: TelemetryBatch, request: Request) -> dict[str, Any]:
    tier = _get_tier(request)
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not api_key:
        api_key = request.query_params.get("api_key", "")

    limit = get_rate_limit_for_tier(tier)
    current = get_rate_limit(conn, api_key or "anonymous")
    if current >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({limit}/min for {tier} tier). Upgrade at openplan.cc",
        )

    accepted = 0
    rejected: list[dict[str, Any]] = []
    for ev in batch.events:
        ac = ev.actual_cost
        ec = ev.expected_cost
        if ac <= 0:
            rejected.append({"reason": "actual_cost must be > 0", "event": ev.model_dump()})
            continue
        if ec and (ac > ec * 10 or ac < ec * 0.01):
            rejected.append({"reason": "actual_cost out of expected range (0.01x-10x)", "event": ev.model_dump()})
            continue
        insert_event(conn, api_key, ev.model_dump())
        increment_rate_limit(conn, api_key or "anonymous")
        accepted += 1

    conn.commit()
    result: dict[str, Any] = {"ok": True, "accepted": accepted}
    if rejected:
        result["rejected"] = rejected
    return result


@v1.get("/baselines", response_model=CalibrationResponse)
async def calibration() -> CalibrationResponse:
    baselines = get_calibration(conn)
    return CalibrationResponse(baselines=[Baseline(**b) for b in baselines])


# ─── GitHub OAuth Device Code Flow ───────────────────────────────────────────

@v1.post("/auth/device")
async def oauth_authorize() -> dict[str, Any]:
    try:
        gh_resp = await start_github_device_flow()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {e}")

    create_oauth_session(conn, gh_resp["device_code"], gh_resp["user_code"], gh_resp.get("expires_in", 600))

    return {
        "device_code": gh_resp["device_code"],
        "user_code": gh_resp["user_code"],
        "verification_uri": gh_resp.get("verification_uri", "https://github.com/login/device"),
        "interval": gh_resp.get("interval", 5),
    }


@v1.post("/auth/device/poll")
async def oauth_token(request: Request) -> dict[str, Any]:
    body = await request.json()
    device_code = body.get("device_code")
    grant_type = body.get("grant_type", "")

    if not device_code:
        raise HTTPException(status_code=400, detail="Missing device_code")

    session = poll_oauth_session(conn, device_code)
    if not session:
        raise HTTPException(status_code=400, detail="Invalid device_code")

    if session["state"] == "completed":
        return {
            "access_token": session["access_token"],
            "refresh_token": session["refresh_token"],
            "token_type": "bearer",
        }

    if time.time() > session["expires_at"]:
        return {"error": "expired_token", "error_description": "Session expired"}

    try:
        gh_resp = await poll_github_token(device_code)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {e}")

    if "access_token" in gh_resp:
        github_token = gh_resp["access_token"]
        gh_user = await get_github_user(github_token)

        existing = get_user_by_github_id(conn, gh_user["id"])
        if existing:
            user_id = existing["id"]
        else:
            user_id = create_user(conn, gh_user["id"], gh_user.get("login", ""), gh_user.get("email", ""), gh_user.get("avatar_url", ""))

        from datetime import datetime, timezone
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)

        complete_oauth_session(conn, device_code, github_token, access_token, refresh_token)
        # Clear GitHub token from DB after use — no longer needed
        conn.execute("UPDATE oauth_sessions SET github_token = '' WHERE device_code = ?", (device_code,))
        conn.commit()

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        }

    error = gh_resp.get("error", "authorization_pending")
    if error == "authorization_pending":
        return {"error": "authorization_pending"}
    if error == "slow_down":
        return {"error": "slow_down"}
    return {"error": error}


# ─── API Key Management ──────────────────────────────────────────────────────

@v1.post("/api/keys")
async def create_api_key(request: Request) -> dict[str, str]:
    auth = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not auth:
        raise HTTPException(status_code=401, detail="Missing access token")

    body = await request.json()
    tier = body.get("tier", "pro")

    session = conn.execute(
        "SELECT * FROM oauth_sessions WHERE access_token = ? AND state = 'completed' ORDER BY created_at DESC LIMIT 1",
        (auth,),
    ).fetchone()
    if not session:
        raise HTTPException(status_code=401, detail="Invalid access token")

    user_row = conn.execute(
        "SELECT id FROM users WHERE id = (SELECT user_id FROM oauth_sessions WHERE access_token = ?)",
        (auth,),
    ).fetchone()
    user_id = user_row["id"] if user_row else ""

    api_key = generate_api_key(conn, user_id=user_id, tier=tier, label="default")
    return {"api_key": api_key, "tier": tier}


@v1.post("/api/keys/revoke")
async def revoke_key(request: Request) -> dict[str, bool]:
    auth = request.headers.get("Authorization", "").replace("Bearer ", "")
    body = await request.json()
    api_key = body.get("api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="Missing api_key")
    # Only the key owner or an admin can revoke
    if auth != api_key and auth != os.environ.get("OPENPLAN_ADMIN_KEY", ""):
        raise HTTPException(status_code=403, detail="Forbidden")
    ok = revoke_api_key(conn, api_key)
    return {"ok": ok}


@v1.get("/api/keys/usage")
async def key_usage(request: Request) -> dict[str, Any]:
    auth = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not auth:
        raise HTTPException(status_code=401, detail="Missing API key")
    return get_key_usage(conn, auth)


# ─── Stripe Checkout + Subscription ──────────────────────────────────────────

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")


@v1.post("/subscribe")
async def create_checkout(request: Request) -> dict[str, str]:
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Billing not configured")

    import stripe as stripe_sdk
    stripe_sdk.api_key = STRIPE_SECRET_KEY

    body = await request.json()
    plan = body.get("plan", "pro")
    api_key = body.get("api_key", "")

    if not api_key:
        raise HTTPException(status_code=400, detail="Missing api_key")

    price_ids = {"pro": os.environ.get("STRIPE_PRO_PRICE_ID", ""), "enterprise": os.environ.get("STRIPE_ENTERPRISE_PRICE_ID", "")}
    price_id = price_ids.get(plan)
    if not price_id:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan}")

    ref_id = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    session = stripe_sdk.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=ref_id,
        success_url=os.environ.get("CHECKOUT_SUCCESS_URL", "https://openplan.cc/success"),
        cancel_url=os.environ.get("CHECKOUT_CANCEL_URL", "https://openplan.cc/pricing"),
    )

    # Map the ref_id back to the API key for webhook resolution
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (f"stripe_ref:{ref_id}", api_key),
    )
    conn.commit()

    return {"checkout_url": session.url, "session_id": session.id}


@v1.post("/webhooks/stripe")
async def stripe_webhook(request: Request) -> dict[str, bool]:
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Billing not configured")

    import stripe as stripe_sdk
    stripe_sdk.api_key = STRIPE_SECRET_KEY

    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    try:
        event = stripe_sdk.Webhook.construct_event(payload, sig_header, webhook_secret)
    except stripe_sdk.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        ref_id = session.get("client_reference_id", "")
        stripe_customer_id = session.get("customer", "")
        stripe_sub_id = session.get("subscription", "")
        tier = "pro"

        # Look up API key from reference hash
        ref_row = conn.execute("SELECT value FROM meta WHERE key = ?", (f"stripe_ref:{ref_id}",)).fetchone()
        api_key = ref_row["value"] if ref_row else ""

        key_row = conn.execute("SELECT user_id FROM api_keys WHERE key = ?", (api_key,)).fetchone()
        if key_row and stripe_sub_id:
            create_subscription(conn, stripe_sub_id, key_row["user_id"], stripe_customer_id, tier)
            conn.execute("UPDATE api_keys SET tier = ? WHERE key = ?", (tier, api_key))
            conn.commit()
            _log.info("Subscription activated for key %s", api_key[:8])

    elif event["type"] == "customer.subscription.deleted":
        sub = event["data"]["object"]
        cancel_subscription(conn, sub["id"])
        _log.info("Subscription canceled: %s", sub["id"])

    return {"ok": True}


@v1.get("/account")
async def subscription_status(request: Request) -> dict[str, Any]:
    auth = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not auth:
        raise HTTPException(status_code=401, detail="Missing API key")

    key_row = conn.execute("SELECT user_id FROM api_keys WHERE key = ?", (auth,)).fetchone()
    if not key_row:
        raise HTTPException(status_code=404, detail="API key not found")

    sub = get_subscription(conn, key_row["user_id"])
    if sub:
        return {"status": sub["status"], "tier": sub["tier"], "current_period_end": sub["current_period_end"]}
    return {"status": "none", "tier": "free"}


# ─── Admin ────────────────────────────────────────────────────────────────────

@v1.post("/admin/keys")
async def admin_create_key(request: Request, tier: str = "free", label: str = "") -> dict[str, str]:
    admin_key = os.environ.get("OPENPLAN_ADMIN_KEY", "")
    auth = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not admin_key or auth != admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")
    key = generate_api_key(conn, tier=tier, label=label)
    return {"api_key": key, "tier": tier, "label": label}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
