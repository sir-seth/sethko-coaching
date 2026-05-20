"""
Vice log routes (T-22). Privacy contract: category labels never leave this module
in any coaching, analytics, or HealthKit payload.

POST   /api/vice/{user_id}                     log a vice (timestamp + optional category_id)
GET    /api/vice/{user_id}/categories          list user's private categories
POST   /api/vice/{user_id}/categories          add a category
DELETE /api/vice/{user_id}/categories/{cat_id} soft-delete a category
GET    /api/vice/{user_id}/recent              last 90 days (timestamp + category_id — NO labels)
DELETE /api/vice/{user_id}/{log_id}            delete a log entry
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

import db
from auth import require_api_key
from models import ViceCategory, ViceCategoryRequest, ViceLogEntry, ViceLogResponse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/vice", tags=["vice"])


@router.post("/{user_id}", response_model=ViceLogResponse, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_api_key)])
async def log_vice(user_id: str, body: dict):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    category_id: Optional[int] = body.get("category_id")
    entry = await db.log_vice(user_id, category_id)
    log.info("Vice logged for user=%s (category_id=%s)", user_id, category_id)
    return entry


@router.get("/{user_id}/categories", response_model=list[ViceCategory],
            dependencies=[Depends(require_api_key)])
async def get_categories(user_id: str):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    return await db.get_vice_categories(user_id)


@router.post("/{user_id}/categories", response_model=ViceCategory, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_api_key)])
async def add_category(user_id: str, payload: ViceCategoryRequest):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    if not payload.label.strip():
        raise HTTPException(status_code=422, detail="label is required.")
    return await db.add_vice_category(user_id, payload.label.strip())


@router.delete("/{user_id}/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_api_key)])
async def delete_category(user_id: str, category_id: int):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    deleted = await db.delete_vice_category(user_id, category_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Category {category_id} not found.")


@router.get("/{user_id}/recent", response_model=list[ViceLogEntry],
            dependencies=[Depends(require_api_key)])
async def get_recent(user_id: str):
    """
    Returns last 90 days of vice log entries. Includes category_id and category_label
    so the in-app private log can display them — but this endpoint must never be
    referenced from coaching, analytics, or any external system.
    """
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    return await db.get_recent_vice(user_id)


@router.delete("/{user_id}/{log_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_api_key)])
async def delete_log_entry(user_id: str, log_id: int):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found.")
    deleted = await db.delete_vice_log_entry(user_id, log_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Log entry {log_id} not found.")
