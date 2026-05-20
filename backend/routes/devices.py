"""
Device management routes (T-25)

POST   /api/devices/oura/connect    → returns Oura PKCE authorize URL
GET    /api/devices/oura/callback   → exchanges code, stores tokens, activates device
DELETE /api/devices/oura            → disconnects Oura, clears tokens
GET    /api/devices                 → { device, synced_at, error } for the masthead chip

The callback redirect_uri must match what is registered in the Oura developer portal.
Set OURA_REDIRECT_URI in Railway env vars, e.g.:
    https://sethko-coaching-production.up.railway.app/api/devices/oura/callback
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse, JSONResponse

import db
from auth import require_api_key
from jobs.oura_pull import (
    build_authorize_url,
    exchange_code,
    generate_pkce_pair,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/devices", tags=["devices"])


def _redirect_uri() -> str:
    base = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or os.environ.get("BASE_URL", "http://localhost:8000")
    if not base.startswith("http"):
        base = f"https://{base}"
    return f"{base}/api/devices/oura/callback"


# ---------------------------------------------------------------------------
# Start Oura OAuth (PKCE)
# ---------------------------------------------------------------------------

@router.post("/oura/connect", dependencies=[Depends(require_api_key)])
async def oura_connect(user_id: str = Query(...)):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")

    verifier, challenge = generate_pkce_pair()
    await db.save_oura_pkce_verifier(user_id, verifier)

    url = build_authorize_url(
        code_challenge=challenge,
        state=user_id,          # state param = user_id so callback knows who to update
        redirect_uri=_redirect_uri(),
    )
    return {"authorize_url": url}


# ---------------------------------------------------------------------------
# Oura OAuth callback (no auth — Oura redirects here directly)
# ---------------------------------------------------------------------------

@router.get("/oura/callback")
async def oura_callback(
    code:  str = Query(...),
    state: str = Query(...),    # user_id
    error: str | None = Query(None),
):
    if error:
        log.error("Oura OAuth error: %s", error)
        return JSONResponse({"error": error}, status_code=400)

    user_id = state
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")

    verifier = await db.get_oura_pkce_verifier(user_id)
    if not verifier:
        raise HTTPException(status_code=400, detail="No pending PKCE verifier found. Restart the connect flow.")

    try:
        tokens = exchange_code(code, verifier, _redirect_uri())
    except Exception as exc:
        log.error("Oura code exchange failed for user=%s: %s", user_id, exc)
        raise HTTPException(status_code=502, detail="Token exchange failed.")

    await db.save_oura_tokens(user_id, tokens)
    await db.clear_oura_pkce_verifier(user_id)
    await db.update_user_device(user_id, "oura")
    await db.update_sync_state(user_id, {"error": None})

    log.info("Oura connected for user=%s", user_id)
    return {"connected": True, "device": "oura"}


# ---------------------------------------------------------------------------
# Disconnect Oura
# ---------------------------------------------------------------------------

@router.delete("/oura", dependencies=[Depends(require_api_key)])
async def oura_disconnect(user_id: str = Query(...)):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    await db.disconnect_oura(user_id)
    log.info("Oura disconnected for user=%s", user_id)
    return {"disconnected": True}


# ---------------------------------------------------------------------------
# Device status (masthead chip)
# ---------------------------------------------------------------------------

@router.get("", dependencies=[Depends(require_api_key)])
async def get_device_status(user_id: str = Query(...)):
    """
    Returns { device, synced_at, error } for the masthead chip.
    device: "whoop" | "oura" | null
    synced_at: ISO 8601 or null
    error: string or null (e.g. "needs_reauth")
    """
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")

    status = await db.get_device_status(user_id)

    # Whoop users: use recovery_source as device fallback if device column is null.
    if status["device"] is None and user.recovery_source == "whoop":
        status["device"] = "whoop"

    return status
