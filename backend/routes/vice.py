"""
Vice log routes (T-22). Privacy contract: category labels never leave this module
in any coaching, analytics, or HealthKit payload.

POST   /api/vice                     log a vice (timestamp + optional category_id)
GET    /api/vice/categories          list user's private categories
POST   /api/vice/categories          add a category
DELETE /api/vice/categories/{cat_id} soft-delete a category
GET    /api/vice/recent              last 90 days (timestamp + category_id — NO labels)
DELETE /api/vice/{log_id}            delete a log entry
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

import db
from auth import get_current_user_id
from models import ViceCategory, ViceCategoryRequest, ViceLogEntry, ViceLogResponse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/vice", tags=["vice"])


@router.post("", response_model=ViceLogResponse, status_code=status.HTTP_201_CREATED)
async def log_vice(body: dict, user_id: str = Depends(get_current_user_id)):
    category_id: Optional[int] = body.get("category_id")
    entry = await db.log_vice(user_id, category_id)
    log.info("Vice logged for user=%s (category_id=%s)", user_id, category_id)
    return entry


@router.get("/categories", response_model=list[ViceCategory])
async def get_categories(user_id: str = Depends(get_current_user_id)):
    return await db.get_vice_categories(user_id)


@router.post("/categories", response_model=ViceCategory, status_code=status.HTTP_201_CREATED)
async def add_category(payload: ViceCategoryRequest, user_id: str = Depends(get_current_user_id)):
    if not payload.label.strip():
        raise HTTPException(status_code=422, detail="label is required.")
    return await db.add_vice_category(user_id, payload.label.strip())


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(category_id: int, user_id: str = Depends(get_current_user_id)):
    deleted = await db.delete_vice_category(user_id, category_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Category {category_id} not found.")


@router.get("/recent", response_model=list[ViceLogEntry])
async def get_recent(user_id: str = Depends(get_current_user_id)):
    """
    Returns last 90 days of vice log entries. Includes category_id and category_label
    so the in-app private log can display them — but this endpoint must never be
    referenced from coaching, analytics, or any external system.
    """
    return await db.get_recent_vice(user_id)


@router.delete("/{log_id:int}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_log_entry(log_id: int, user_id: str = Depends(get_current_user_id)):
    deleted = await db.delete_vice_log_entry(user_id, log_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Log entry {log_id} not found.")
