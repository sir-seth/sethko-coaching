"""
Food log routes (T-21).

POST /api/food/parse-voice   transcript → [ParsedFoodItem]
POST /api/food/log           log one food entry
GET  /api/food/entries       entries for a date
GET  /api/food/macros        day totals
GET  /api/food/week-status   7-day strip statuses
"""

import json
import logging
import os
from datetime import date, timedelta

import anthropic
from fastapi import APIRouter, Depends, HTTPException, Query

import db
from auth import get_current_user_id
from lib.food_db import search_usda, scale_macros
from models import (
    DayMacros,
    FoodEntry,
    FoodEntryRequest,
    ParsedFoodItem,
    WeekDayStatus,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/food", tags=["food"])

_HAIKU = "claude-haiku-4-5-20251001"

# Claude tool schema: parses transcript into structured food items.
_PARSE_TOOL = {
    "name": "parse_food_items",
    "description": (
        "Extract individual food items from a spoken meal description. "
        "For each item estimate its weight in grams."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "description": "Short food name, e.g. 'Greek yogurt'"},
                        "weight_g": {"type": "number", "description": "Estimated weight in grams"},
                    },
                    "required": ["description", "weight_g"],
                },
            }
        },
        "required": ["items"],
    },
}


async def _resolve_macros(description: str, weight_g: float) -> dict:
    """
    Try cache → USDA lookup. If USDA has no match, return zeros (caller
    will use Claude's estimate as fallback via the caller's own logic).
    """
    cached = await db.lookup_cached_food(description)
    if cached:
        return scale_macros(cached, weight_g)

    usda = await search_usda(description)
    if usda:
        await db.cache_food(description, usda)
        return scale_macros(usda, weight_g)

    return {"kcal": None, "protein_g": None, "fat_g": None, "carbs_g": None}


@router.post(
    "/parse-voice",
    response_model=list[ParsedFoodItem],
)
async def parse_voice(body: dict, user_id: str = Depends(get_current_user_id)):
    """
    Body: { "transcript": "two eggs and oats and a black coffee" }
    Returns a list of ParsedFoodItem with USDA-verified macros where available.
    """
    transcript = body.get("transcript", "").strip()
    if not transcript:
        raise HTTPException(status_code=422, detail="transcript is required.")

    client = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    message = await client.messages.create(
        model=_HAIKU,
        max_tokens=512,
        tools=[_PARSE_TOOL],
        tool_choice={"type": "tool", "name": "parse_food_items"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Parse this meal description into individual food items with weight estimates: "
                    f'"{transcript}"'
                ),
            }
        ],
    )

    # Extract tool use block
    parsed_items = []
    for block in message.content:
        if block.type == "tool_use" and block.name == "parse_food_items":
            raw_items = block.input.get("items", [])
            for item in raw_items:
                desc = item.get("description", "")
                weight = float(item.get("weight_g", 100))
                macros = await _resolve_macros(desc, weight)
                parsed_items.append(
                    ParsedFoodItem(
                        description=desc,
                        weight_g=weight,
                        kcal=macros.get("kcal"),
                        protein_g=macros.get("protein_g"),
                        fat_g=macros.get("fat_g"),
                        carbs_g=macros.get("carbs_g"),
                    )
                )
            break

    log.info("parse-voice for user=%s transcript=%r → %d items", user_id, transcript, len(parsed_items))
    return parsed_items


@router.post(
    "/log",
    response_model=FoodEntry,
)
async def log_food(payload: FoodEntryRequest, user_id: str = Depends(get_current_user_id)):
    entry = await db.insert_food_entry(user_id, payload)
    log.info("Logged food for user=%s: %s (%s kcal)", user_id, entry.description, entry.kcal)
    return entry


@router.get(
    "/entries",
    response_model=list[FoodEntry],
)
async def get_entries(
    date: date = Query(default=None),
    user_id: str = Depends(get_current_user_id),
):
    target = date or __import__("datetime").date.today()
    return await db.get_food_entries(user_id, target)


@router.get(
    "/macros",
    response_model=DayMacros,
)
async def get_macros(
    date: date = Query(default=None),
    user_id: str = Depends(get_current_user_id),
):
    target = date or __import__("datetime").date.today()
    return await db.get_day_macros(user_id, target)


@router.get(
    "/week-status",
    response_model=list[WeekDayStatus],
)
async def get_week_status(
    anchor: date = Query(default=None),
    user_id: str = Depends(get_current_user_id),
):
    """Returns statuses for the 7-day week containing `anchor` (defaults to today)."""
    anchor_date = anchor or __import__("datetime").date.today()
    monday = anchor_date - timedelta(days=anchor_date.weekday())
    week = [monday + timedelta(days=i) for i in range(7)]
    return await db.get_week_food_status(user_id, week)
