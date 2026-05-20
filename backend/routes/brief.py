"""GET /api/brief/today/{user_id}"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

import db
from auth import require_api_key

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/brief", tags=["brief"])


@router.get("/today/{user_id}", dependencies=[Depends(require_api_key)])
async def get_brief_today(user_id: str):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")

    payload = await db.get_brief_today(user_id, date.today())

    if payload is None:
        # Morning job hasn't run yet for today.
        return JSONResponse(status_code=404, content={"state": "not_ready"})

    # Edge states from the job (still_learning, needs_mode) — pass through as 404.
    if "state" in payload:
        return JSONResponse(status_code=404, content=payload)

    return JSONResponse(status_code=200, content=payload)
