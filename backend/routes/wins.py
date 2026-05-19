"""POST /api/wins/{user_id}, GET /api/wins/{user_id}"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

import db
from auth import require_api_key
from models import WinEntry, WinRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wins", tags=["wins"])


@router.post("/{user_id}", response_model=WinEntry, dependencies=[Depends(require_api_key)])
async def post_win(user_id: str, payload: WinRequest):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    win = await db.insert_win(user_id, payload)
    log.info("Stored win for user=%s date=%s category=%s", user_id, payload.date, payload.category)
    return win


@router.get("/{user_id}", response_model=list[WinEntry], dependencies=[Depends(require_api_key)])
async def get_wins(
    user_id: str,
    date: date = Query(default=None, description="YYYY-MM-DD; defaults to yesterday"),
):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    from datetime import date as date_type, timedelta
    target = date if date is not None else date_type.today() - timedelta(days=1)
    return await db.get_wins(user_id, target)
