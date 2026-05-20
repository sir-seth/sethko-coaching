"""POST /api/health/ingest  |  GET /api/health/metrics/weight"""

import logging

from fastapi import APIRouter, Depends, Query

import db
from auth import get_current_user_id
from models import HealthSnapshot

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/health", tags=["health"])


@router.post("/ingest")
async def ingest_health_data(payload: HealthSnapshot, user_id: str = Depends(get_current_user_id)):
    await db.upsert_health_metrics(user_id, payload)
    log.info("Stored health data for user=%s date=%s", user_id, payload.date)
    return {"stored": True, "date": payload.date}


@router.get("/metrics/weight")
async def get_weight_metrics(
    days: int = Query(default=30, ge=1, le=90),
    user_id: str = Depends(get_current_user_id),
):
    """30-day weight trend for the Patterns tab sparkline."""
    trend = await db.get_weight_trend(user_id, days=days)
    return {
        "points": [{"date": p.date.isoformat(), "weight_lbs": p.weight_lbs} for p in trend],
        "days": days,
    }
