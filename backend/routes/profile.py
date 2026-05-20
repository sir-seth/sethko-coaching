"""GET /api/profile/{user_id}  ·  PATCH /api/profile/{user_id}"""

import logging

from fastapi import APIRouter, Depends, HTTPException

import db
from auth import require_api_key
from models import ModeUpdateRequest, User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("/{user_id}", response_model=User, dependencies=[Depends(require_api_key)])
async def get_profile(user_id: str):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    return user


@router.patch("/{user_id}", dependencies=[Depends(require_api_key)])
async def update_mode(user_id: str, payload: ModeUpdateRequest):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    await db.update_user_mode(user_id, payload.mode)
    log.info("Mode updated: user=%s mode=%s", user_id, payload.mode)
    return {"mode": payload.mode}
