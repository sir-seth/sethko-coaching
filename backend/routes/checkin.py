"""POST /api/checkin/{user_id}, GET /api/checkin/{user_id}/today"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

import db
from auth import require_api_key
from models import CheckInRequest, SubjectiveLog

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/checkin", tags=["checkin"])


@router.post("/{user_id}", dependencies=[Depends(require_api_key)])
async def post_checkin(user_id: str, payload: CheckInRequest):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    await db.upsert_subjective_log(user_id, payload)
    log.info("Stored check-in for user=%s date=%s mood=%s", user_id, payload.date, payload.mood)
    return {"stored": True, "date": payload.date}


@router.get("/{user_id}/today", response_model=SubjectiveLog, dependencies=[Depends(require_api_key)])
async def get_checkin_today(user_id: str):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    entry = await db.get_subjective_log(user_id, date.today())
    if not entry:
        raise HTTPException(status_code=404, detail="No check-in for today.")
    return entry
