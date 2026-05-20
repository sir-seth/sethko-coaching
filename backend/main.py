"""
Sethko Coaching — FastAPI backend

Routes
------
POST /api/auth/siwa                 Sign in with Apple → session token (T-26)
POST /api/health/ingest             iOS app sends HealthKit snapshot
GET  /api/brief/today               iOS fetches today's brief (cached or generated)
POST /coaching/generate/{user_id}   Railway cron triggers generation (internal)
GET  /users/{user_id}               App reads user profile (internal)
GET  /health                        Health check (Railway)
GET  /healthz                       Health check alias

Auth
----
User-facing routes: Bearer session token (from SIWA exchange).
Cron/internal routes: X-API-Key shared secret.
Dev/non-production: X-Seed-User header bypasses SIWA (seed users only).
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

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
# Session middleware — resolves Bearer token or X-Seed-User → request.state.user_id
# ---------------------------------------------------------------------------

_SEED_USERS = {"seth", "slav", "oura_test"}


class _SessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
            if token:
                user_id = await db.resolve_session(token)
                if user_id:
                    request.state.user_id = user_id
        elif os.environ.get("ENV") != "production":
            seed = request.headers.get("X-Seed-User", "").strip()
            if seed in _SEED_USERS:
                request.state.user_id = seed
        return await call_next(request)


app.add_middleware(_SessionMiddleware)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

from routes.auth import router as auth_router, users_router
from routes.health import router as health_router
from routes.brief import router as brief_router
from routes.checkin import router as checkin_router
from routes.wins import router as wins_router
from routes.log import router as log_router
from routes.food import router as food_router
from routes.vice import router as vice_router
from routes.profile import router as profile_router
from routes.devices import router as devices_router
from routes.plan import router as plan_router
from routes.workouts import router as workouts_router
from routes.patterns import router as patterns_router
from routes.mood_wins import router as mood_wins_router
from routes.push import router as push_router

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(health_router)
app.include_router(brief_router)
app.include_router(checkin_router)
app.include_router(wins_router)
app.include_router(log_router)
app.include_router(food_router)
app.include_router(vice_router)
app.include_router(profile_router)
app.include_router(devices_router)
app.include_router(plan_router)
app.include_router(workouts_router)
app.include_router(patterns_router)
app.include_router(mood_wins_router)
app.include_router(push_router)


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
