"""
Sethko Coaching — FastAPI backend

Routes
------
POST /health-data/:user_id          iOS app sends HealthKit snapshot after each read
GET  /coaching/today/:user_id       iOS fetches today's brief; generates if stale
POST /coaching/generate/:user_id    Cron calls this at 7am local for each user
GET  /users/:user_id                App reads user profile (modality, goal, etc.)
GET  /health                        Railway health check

Auth
----
All routes require an X-API-Key header matching the API_KEY env var.
Simple shared secret — good enough for a two-user personal tool. Phase 2
replaces this with Sign in with Apple + JWT when real auth lands.
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse

import db
import whoop
import coach
from models import (
    CoachingResponse,
    HealthSnapshot,
    User,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    log.info("Database initialised.")
    yield
    await db.close()


app = FastAPI(title="Sethko Coaching API", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

def require_api_key(x_api_key: str = Header(...)):
    expected = os.environ.get("API_KEY")
    if not expected:
        raise HTTPException(status_code=500, detail="API_KEY not configured on server.")
    if x_api_key != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key.")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    """Railway / load balancer health check. No auth required."""
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/users/{user_id}", response_model=User, dependencies=[Depends(require_api_key)])
async def get_user(user_id: str):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    return user


@app.post("/health-data/{user_id}", dependencies=[Depends(require_api_key)])
async def receive_health_data(user_id: str, payload: HealthSnapshot):
    """
    iOS app POSTs HealthKit data here after each on-device read.
    Stores weight and workouts; existing rows for the same date are upserted.
    """
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")

    await db.upsert_health_metrics(user_id, payload)
    log.info("Stored health data for user=%s date=%s", user_id, payload.date)
    return {"stored": True, "date": payload.date}


@app.get(
    "/coaching/today/{user_id}",
    response_model=CoachingResponse,
    dependencies=[Depends(require_api_key)],
)
async def get_coaching_today(user_id: str):
    """
    Returns today's coaching brief. If one has already been generated today,
    returns the cached version. If not (e.g. cron hasn't run yet, or user
    opened the app before 7am), generates it on-demand.
    """
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")

    today = date.today()
    cached = await db.get_coaching_output(user_id, today)
    if cached:
        log.info("Returning cached coaching for user=%s date=%s", user_id, today)
        return cached

    log.info("No cached coaching for user=%s — generating now.", user_id)
    return await _generate_and_store(user_id, user)


@app.post(
    "/coaching/generate/{user_id}",
    response_model=CoachingResponse,
    dependencies=[Depends(require_api_key)],
)
async def generate_coaching(user_id: str):
    """
    Called by the Railway cron job at 7am local time for each user.
    Always regenerates (doesn't check cache) so the cron is idempotent.
    """
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")

    log.info("Cron-triggered coaching generation for user=%s", user_id)
    return await _generate_and_store(user_id, user)


# ---------------------------------------------------------------------------
# Internal: generation pipeline
# ---------------------------------------------------------------------------

async def _generate_and_store(user_id: str, user: User) -> CoachingResponse:
    """
    Full pipeline:
      1. Refresh Whoop token if needed, pull latest data
      2. Load recent HealthKit data from DB
      3. Build the digest Claude receives
      4. Call Claude, validate response
      5. Store in coaching_outputs
      6. Return as CoachingResponse
    """
    # Step 1 — Whoop
    whoop_snapshot = None
    try:
        tokens = await db.get_whoop_tokens(user_id)
        if tokens:
            tokens = await whoop.refresh_if_needed(tokens)
            await db.save_whoop_tokens(user_id, tokens)
            whoop_snapshot = await whoop.fetch_recent(tokens["access_token"])
            await db.upsert_recovery_signal(user_id, whoop_snapshot)
    except Exception as exc:
        log.warning("Whoop pull failed for user=%s: %s", user_id, exc)
        # Non-fatal — proceed with whatever is in the DB

    # Step 2 — Load from DB
    recovery = await db.get_latest_recovery_signal(user_id)
    health = await db.get_latest_health_metrics(user_id)
    recent_workouts = await db.get_recent_workouts(user_id, days=7)
    weight_trend = await db.get_weight_trend(user_id, days=30)

    # Step 3 — Build digest
    digest = coach.build_digest(
        user=user,
        recovery=recovery,
        health=health,
        recent_workouts=recent_workouts,
        weight_trend=weight_trend,
    )

    # Step 4 — Call Claude
    result = await coach.generate_coaching(
        digest=digest,
        modality=user.dietary_modality,
        goal=user.goal,
    )

    # Step 5 — Store
    await db.save_coaching_output(user_id, digest, result)
    log.info("Coaching generated and stored for user=%s", user_id)

    # Step 6 — Return
    return CoachingResponse(**result)
