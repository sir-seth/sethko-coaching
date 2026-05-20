"""POST /api/workouts — manual workout logger (T-29)"""

import logging
from datetime import date

from fastapi import APIRouter, Depends

import db
from auth import get_current_user_id
from models import WinRequest, WorkoutLogRequest, WorkoutLogResponse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/workouts", tags=["workouts"])


@router.post("", response_model=WorkoutLogResponse)
async def log_workout(body: WorkoutLogRequest, user_id: str = Depends(get_current_user_id)):
    """
    Persist a logger-recorded lift session.
    Inserts workout_log row, per-set workout_sets rows, and a win_log row for today.
    """
    result = await db.insert_manual_workout(user_id, body)

    # Auto-create a win for the day
    focus_names = list({ex.name for ex in body.exercises if not ex.skipped})
    win_text = _win_text(body, focus_names)
    win_meta = f"{result['exercise_count']} exercises · {result['set_count']} sets"
    try:
        win = await db.insert_win(user_id, WinRequest(
            date=date.today(),
            text=win_text,
            meta=win_meta,
            category="lift",
        ))
        win_id = win.id
    except Exception:
        log.warning("Could not create win for workout user=%s", user_id)
        win_id = None

    log.info(
        "Manual workout logged: user=%s workout_id=%s exercises=%d sets=%d",
        user_id, result["workout_id"], result["exercise_count"], result["set_count"],
    )
    return WorkoutLogResponse(
        workout_id=result["workout_id"],
        win_id=win_id,
        set_count=result["set_count"],
        exercise_count=result["exercise_count"],
    )


def _win_text(body: WorkoutLogRequest, focus_names: list[str]) -> str:
    if body.notes and len(body.notes) > 10:
        return body.notes[:120]
    if not focus_names:
        return "Lifted today."
    if len(focus_names) == 1:
        return f"Lifted today. {focus_names[0]}."
    return f"Lifted today. {', '.join(focus_names[:-1])} and {focus_names[-1]}."
