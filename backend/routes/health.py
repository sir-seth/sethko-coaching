"""POST /api/health/ingest/{user_id}"""

import logging

from fastapi import APIRouter, Depends, HTTPException

import db
from auth import require_api_key
from models import HealthSnapshot

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/health", tags=["health"])


@router.post("/ingest/{user_id}", dependencies=[Depends(require_api_key)])
async def ingest_health_data(user_id: str, payload: HealthSnapshot):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    await db.upsert_health_metrics(user_id, payload)
    log.info("Stored health data for user=%s date=%s", user_id, payload.date)
    return {"stored": True, "date": payload.date}
