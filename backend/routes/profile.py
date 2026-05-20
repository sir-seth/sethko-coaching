"""GET /api/profile  ·  PATCH /api/profile"""

import logging

from fastapi import APIRouter, Depends, HTTPException

import db
from auth import get_current_user_id
from models import ModeUpdateRequest, User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=User)
async def get_profile(user_id: str = Depends(get_current_user_id)):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


@router.patch("")
async def update_mode(payload: ModeUpdateRequest, user_id: str = Depends(get_current_user_id)):
    await db.update_user_mode(user_id, payload.mode)
    log.info("Mode updated: user=%s mode=%s", user_id, payload.mode)
    return {"mode": payload.mode}
