"""
Push notification registration and preferences (T-36).

POST   /api/push/register   — upsert device token
DELETE /api/push/register   — remove all tokens for user (opt-out)
GET    /api/push/prefs       — read notify flags + wake window
PATCH  /api/push/prefs       — update notify flags + wake window
"""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

import db
from auth import get_current_user_id

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/push", tags=["push"])

_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_TIME_RE  = re.compile(r"^\d{2}:\d{2}$")


class RegisterRequest(BaseModel):
    device_token: str
    env: str = "sandbox"

    @field_validator("env")
    @classmethod
    def _env(cls, v: str) -> str:
        if v not in ("sandbox", "production"):
            raise ValueError("env must be 'sandbox' or 'production'")
        return v

    @field_validator("device_token")
    @classmethod
    def _token(cls, v: str) -> str:
        clean = v.replace(" ", "")
        if not _TOKEN_RE.match(clean):
            raise ValueError("device_token must be a 64-hex-char APNs token")
        return clean.lower()


class PrefsUpdate(BaseModel):
    notify_brief:   bool | None = None
    notify_checkin: bool | None = None
    wake_window:    str  | None = None

    @field_validator("wake_window")
    @classmethod
    def _wake(cls, v: str | None) -> str | None:
        if v is not None and not _TIME_RE.match(v):
            raise ValueError("wake_window must be HH:MM (24h)")
        return v


@router.post("/register", status_code=204)
async def register_token(
    body: RegisterRequest,
    user_id: str = Depends(get_current_user_id),
):
    await db.register_device_token(user_id, body.device_token, body.env)
    log.info("Push token registered user=%s env=%s", user_id, body.env)


@router.delete("/register", status_code=204)
async def unregister_token(user_id: str = Depends(get_current_user_id)):
    await db.unregister_device_tokens(user_id)
    log.info("Push tokens removed user=%s", user_id)


@router.get("/prefs")
async def get_prefs(user_id: str = Depends(get_current_user_id)):
    return await db.get_push_prefs(user_id)


@router.patch("/prefs", status_code=204)
async def update_prefs(
    body: PrefsUpdate,
    user_id: str = Depends(get_current_user_id),
):
    await db.update_push_prefs(
        user_id,
        notify_brief=body.notify_brief,
        notify_checkin=body.notify_checkin,
        wake_window=body.wake_window,
    )
    log.info("Push prefs updated user=%s", user_id)
