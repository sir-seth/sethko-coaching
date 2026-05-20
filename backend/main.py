"""
Sethko Coaching — FastAPI backend

Routes
------
POST /api/health/ingest/{user_id}   iOS app sends HealthKit snapshot
GET  /api/brief/today/{user_id}     iOS fetches today's brief (cached or generated)
POST /coaching/generate/{user_id}   Railway cron triggers generation
GET  /users/{user_id}               App reads user profile
GET  /health                        Health check (Railway)
GET  /healthz                       Health check alias

Auth
----
All routes require X-API-Key matching the API_KEY env var.
Simple shared secret — good enough for a two-user personal tool.
Phase 2 replaces this with Sign in with Apple + JWT.
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse

import db
import coach
from auth import require_api_key
from models import CoachingResponse, HealthSnapshot, User

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
# Routers
# ---------------------------------------------------------------------------

from routes.health import router as health_router
from routes.brief import router as brief_router
from routes.checkin import router as checkin_router
from routes.wins import router as wins_router
from routes.log import router as log_router
from routes.food import router as food_router

app.include_router(health_router)
app.include_router(brief_router)
app.include_router(checkin_router)
app.include_router(wins_router)
app.include_router(log_router)
app.include_router(food_router)


# ---------------------------------------------------------------------------
# Health checks (no auth)
# ---------------------------------------------------------------------------

@app.get("/health")
@app.get("/healthz")
async def health_check():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@app.get("/users/{user_id}", response_model=User, dependencies=[Depends(require_api_key)])
async def get_user(user_id: str):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    return user


# ---------------------------------------------------------------------------
# Legacy health-data endpoint — kept for backward compat during iOS rollout
# ---------------------------------------------------------------------------

@app.post("/health-data/{user_id}", dependencies=[Depends(require_api_key)])
async def receive_health_data_legacy(user_id: str, payload: HealthSnapshot):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    await db.upsert_health_metrics(user_id, payload)
    log.info("(legacy) Stored health data for user=%s date=%s", user_id, payload.date)
    return {"stored": True, "date": payload.date}


# ---------------------------------------------------------------------------
# Cron-triggered generation
# ---------------------------------------------------------------------------

@app.post(
    "/coaching/generate/{user_id}",
    response_model=CoachingResponse,
    dependencies=[Depends(require_api_key)],
)
async def generate_coaching(user_id: str):
    """Called by Railway cron via jobs/morning.py. Always regenerates."""
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    log.info("Cron-triggered generation for user=%s", user_id)
    return await coach.run_pipeline(user_id, user)


# ---------------------------------------------------------------------------
# Legacy coaching endpoint — kept for backward compat during iOS rollout
# ---------------------------------------------------------------------------

@app.get(
    "/coaching/today/{user_id}",
    response_model=CoachingResponse,
    dependencies=[Depends(require_api_key)],
)
async def get_coaching_today_legacy(user_id: str):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    cached = await db.get_coaching_output(user_id, date.today())
    if cached:
        return cached
    return await coach.run_pipeline(user_id, user)
