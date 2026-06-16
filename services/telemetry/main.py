from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .auth import get_tier_from_api_key, get_rate_limit_for_tier
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


def _get_tier(request: Request) -> str:
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not api_key:
        api_key = request.query_params.get("api_key", "")
    tier = get_tier_from_api_key(conn, api_key)
    if not tier and os.environ.get("OPENPLAN_REQUIRE_API_KEY"):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return tier or "free"


@app.get("/health")
async def health() -> HealthResponse:
    count = conn.execute("SELECT COUNT(*) AS cnt FROM calibration_events").fetchone()
    return HealthResponse(
        ok=True,
        events_count=count["cnt"] if count else 0,
        version=VERSION,
    )


@app.post("/telemetry")
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
            detail=f"Rate limit exceeded ({limit}/min for {tier} tier). Upgrade at openplan.ai",
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


@app.get("/calibration", response_model=CalibrationResponse)
async def calibration() -> CalibrationResponse:
    baselines = get_calibration(conn)
    return CalibrationResponse(
        baselines=[Baseline(**b) for b in baselines]
    )


@app.post("/admin/keys")
async def create_key(tier: str = "free", label: str = "") -> dict[str, str]:
    admin_key = os.environ.get("OPENPLAN_ADMIN_KEY", "")
    if admin_key:
        from .auth import generate_api_key
        key = generate_api_key(conn, tier=tier, label=label)
        return {"api_key": key, "tier": tier, "label": label}
    raise HTTPException(status_code=403, detail="Admin key not configured")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
