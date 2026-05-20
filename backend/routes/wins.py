"""POST /api/wins, GET /api/wins"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, Query

import db
from auth import get_current_user_id
from models import WinEntry, WinRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wins", tags=["wins"])


@router.post("", response_model=WinEntry)
async def post_win(payload: WinRequest, user_id: str = Depends(get_current_user_id)):
    win = await db.insert_win(user_id, payload)
    log.info("Stored win for user=%s date=%s category=%s", user_id, payload.date, payload.category)
    return win


@router.get("", response_model=list[WinEntry])
async def get_wins(
    date: date = Query(default=None, description="YYYY-MM-DD; defaults to yesterday"),
    user_id: str = Depends(get_current_user_id),
):
    from datetime import date as date_type, timedelta
    target = date if date is not None else date_type.today() - timedelta(days=1)
    return await db.get_wins(user_id, target)
