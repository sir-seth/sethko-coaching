"""GET /api/brief/today/{user_id}"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

import db
import coach
from auth import require_api_key
from models import CoachingResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/brief", tags=["brief"])


@router.get(
    "/today/{user_id}",
    response_model=CoachingResponse,
    dependencies=[Depends(require_api_key)],
)
async def get_brief_today(user_id: str):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")

    cached = await db.get_coaching_output(user_id, date.today())
    if cached:
        log.info("Returning cached brief for user=%s", user_id)
        return cached

    log.info("No cached brief for user=%s — generating now.", user_id)
    return await coach.run_pipeline(user_id, user)
