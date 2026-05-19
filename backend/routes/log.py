"""GET /api/log/{user_id}/today, GET /api/log/{user_id}/pending-confirms, POST /api/log/{user_id}/confirm/{workout_id}"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

import db
from auth import require_api_key
from models import LogEntry, PendingWorkout

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/log", tags=["log"])


@router.get("/{user_id}/pending-confirms", response_model=list[PendingWorkout], dependencies=[Depends(require_api_key)])
async def get_pending_confirms(user_id: str):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    return await db.get_pending_workouts(user_id)


@router.get("/{user_id}/today", response_model=list[LogEntry], dependencies=[Depends(require_api_key)])
async def get_today_log(user_id: str):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    return await db.get_today_entries(user_id, date.today())


@router.post("/{user_id}/confirm/{workout_id}", dependencies=[Depends(require_api_key)])
async def confirm_workout(user_id: str, workout_id: int):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    confirmed = await db.confirm_workout(user_id, workout_id)
    if not confirmed:
        raise HTTPException(status_code=404, detail=f"Workout {workout_id} not found for user {user_id!r}.")
    log.info("Confirmed workout id=%s for user=%s", workout_id, user_id)
    return {"confirmed": True, "workout_id": workout_id}
