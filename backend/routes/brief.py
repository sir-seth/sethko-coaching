"""GET /api/brief/today"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

import db
from auth import get_current_user_id

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/brief", tags=["brief"])


@router.get("/today")
async def get_brief_today(user_id: str = Depends(get_current_user_id)):
    payload = await db.get_brief_today(user_id, date.today())

    if payload is None:
        return JSONResponse(status_code=404, content={"state": "not_ready"})

    if "state" in payload:
        return JSONResponse(status_code=404, content=payload)

    return JSONResponse(status_code=200, content=payload)
