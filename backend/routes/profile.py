"""GET /api/profile  ·  PATCH /api/profile"""

import logging

from fastapi import APIRouter, Depends, HTTPException

import db
from auth import get_current_user_id
from models import ModeUpdateRequest, ProfileUpdateRequest, User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=User)
async def get_profile(user_id: str = Depends(get_current_user_id)):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


@router.patch("")
async def update_profile(
    payload: ProfileUpdateRequest, user_id: str = Depends(get_current_user_id)
):
    if payload.mode is not None:
        await db.update_user_mode(user_id, payload.mode)
        log.info("Mode updated: user=%s mode=%s", user_id, payload.mode)

    if any(
        v is not None
        for v in (payload.goal, payload.habits, payload.onboarding_completed_at)
    ):
        await db.update_user_profile(
            user_id,
            goal=payload.goal,
            habits=payload.habits,
            onboarding_completed_at=payload.onboarding_completed_at,
        )
        log.info(
            "Profile updated: user=%s goal=%s habits_count=%s onboarding=%s",
            user_id,
            payload.goal,
            len(payload.habits) if payload.habits is not None else None,
            payload.onboarding_completed_at,
        )

    return {"ok": True}
