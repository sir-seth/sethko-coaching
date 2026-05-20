"""GET /api/log/today, GET /api/log/pending-confirms, POST /api/log/confirm/{workout_id}"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

import db
from auth import get_current_user_id
from models import LogEntry, PendingWorkout

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/log", tags=["log"])


@router.get("/pending-confirms", response_model=list[PendingWorkout])
async def get_pending_confirms(user_id: str = Depends(get_current_user_id)):
    return await db.get_pending_workouts(user_id)


@router.get("/today", response_model=list[LogEntry])
async def get_today_log(user_id: str = Depends(get_current_user_id)):
    return await db.get_today_entries(user_id, date.today())


@router.post("/confirm/{workout_id}")
async def confirm_workout(workout_id: int, user_id: str = Depends(get_current_user_id)):
    confirmed = await db.confirm_workout(user_id, workout_id)
    if not confirmed:
        raise HTTPException(status_code=404, detail=f"Workout {workout_id} not found.")
    log.info("Confirmed workout id=%s for user=%s", workout_id, user_id)
    return {"confirmed": True, "workout_id": workout_id}
