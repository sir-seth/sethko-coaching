"""POST /api/checkin, GET /api/checkin/today"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

import db
from auth import get_current_user_id
from models import CheckInRequest, SubjectiveLog

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/checkin", tags=["checkin"])


@router.post("")
async def post_checkin(payload: CheckInRequest, user_id: str = Depends(get_current_user_id)):
    await db.upsert_subjective_log(user_id, payload)
    log.info("Stored check-in for user=%s date=%s mood=%s", user_id, payload.date, payload.mood)
    return {"stored": True, "date": payload.date}


@router.get("/today", response_model=SubjectiveLog)
async def get_checkin_today(user_id: str = Depends(get_current_user_id)):
    entry = await db.get_subjective_log(user_id, date.today())
    if not entry:
        raise HTTPException(status_code=404, detail="No check-in for today.")
    return entry
