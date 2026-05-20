"""
Auth routes (T-26)

POST /api/auth/siwa        Sign in with Apple token exchange → session token
DELETE /api/users/me       Account deletion (Apple guideline 5.1.1(v))
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

import db
from auth import get_current_user_id
from lib.apple_auth import verify_identity_token
from models import SIWARequest, SIWAResponse

log = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])
users_router = APIRouter(prefix="/api/users", tags=["users"])


@router.post("/api/auth/siwa", response_model=SIWAResponse)
async def sign_in_with_apple(payload: SIWARequest):
    """
    Exchange an Apple identity token for a Sethko session token.

    iOS sends identity_token, authorization_code, user_id_apple (the stable Apple sub),
    raw_nonce (un-hashed), and — on first auth only — email and full_name.

    Backend verifies the token, upserts the user by apple_sub, creates a session,
    and returns session_token + user_id + mode.
    """
    try:
        token_payload = await verify_identity_token(payload.identity_token, payload.raw_nonce)
    except ValueError as exc:
        log.warning("SIWA token verification failed: %s", exc)
        raise HTTPException(status_code=401, detail=str(exc))

    sub = token_payload["sub"]
    if sub != payload.user_id_apple:
        raise HTTPException(status_code=401, detail="user_id_apple does not match token sub")

    user = await db.upsert_user_by_apple_sub(
        apple_sub=sub,
        email=payload.email,
        full_name=payload.full_name,
    )

    session_token = await db.create_session(user["id"])
    log.info("SIWA sign-in: user_id=%s", user["id"])

    return SIWAResponse(
        session_token=session_token,
        user_id=user["id"],
        mode=user.get("mode"),
    )


@users_router.delete("/me", status_code=204)
async def delete_account(user_id: str = Depends(get_current_user_id)):
    """
    Cascade-delete the authenticated user and all their data.
    Required by Apple App Store guideline 5.1.1(v).
    """
    await db.delete_user_cascade(user_id)
    log.info("Account self-deleted: user_id=%s", user_id)
