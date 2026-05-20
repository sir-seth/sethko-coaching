"""GET /api/plan/today, POST /api/plan/commit, GET /api/plan/lift/today, GET /api/plan/walk/today"""

import logging
from datetime import date

from fastapi import APIRouter, Depends

import db
from auth import get_current_user_id
from lib.lift_pool import get_lift_plan
from models import CommitPlanRequest

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/plan", tags=["plan"])


@router.get("/today")
async def get_plan_today(user_id: str = Depends(get_current_user_id)):
    """
    Returns Sethko's pick (from brief_today.payload.plan_pick, set at morning cron)
    plus any user-committed docket items for today.
    """
    brief = await db.get_brief_today(user_id, date.today())
    pick = brief.get("plan_pick") if brief else None
    docket = await db.get_docket_items(user_id, date.today())
    return {"pick": pick, "docket": docket}


@router.post("/commit")
async def commit_plan(body: CommitPlanRequest, user_id: str = Depends(get_current_user_id)):
    """User taps a grid card to add it to today's docket."""
    item = await db.commit_plan(user_id, date.today(), body.category, body.name)
    return item


@router.get("/lift/today")
async def get_lift_today(user_id: str = Depends(get_current_user_id)):
    """
    Returns today's lift session plan.
    Split rotates on ISO week day (Mon=0 … Sun=6) mod 4.
    Gentle mode caps RPE and overrides the headline.
    """
    user = await db.get_user(user_id)
    gentle = (user.mode == "gentle") if user else False
    day_index = date.today().isoweekday() - 1  # Mon=0, Sun=6
    plan = get_lift_plan(day_index, gentle=gentle)
    return plan


@router.get("/walk/today")
async def get_walk_today(user_id: str = Depends(get_current_user_id)):
    """
    Returns today's walk invitation.
    Distance adjusts for rough outlook or recent vice log (softens to 0.4mi).
    Optimizer mode gets a longer default (1.5mi).
    """
    user = await db.get_user(user_id)
    mode = user.mode if user else "gentle"

    # Check outlook from today's brief
    brief = await db.get_brief_today(user_id, date.today())
    is_rough = False
    if brief:
        outlook = (brief.get("outlook") or {}).get("value")
        if isinstance(outlook, (int, float)) and outlook < 30:
            is_rough = True

    # Check for recent vice log (last 24h)
    if not is_rough:
        recent = await db.get_recent_vice(user_id, days=1)
        is_rough = bool(recent)

    if is_rough:
        distance_mi, duration_min, destination = 0.4, 9, "a short loop near you"
    elif mode == "optimizer":
        distance_mi, duration_min, destination = 1.5, 30, "a park nearby"
    else:
        distance_mi, duration_min, destination = 0.8, 18, "a park nearby"

    return {
        "headline":        "A walk to the park.",
        "headline_italic": "That's all.",
        "body":            None,
        "destination": {
            "label":        destination,
            "distance_mi":  distance_mi,
            "duration_min": duration_min,
        },
        "intentions": [
            {"icon": "drop.fill",  "text": "A glass of water before you go"},
            {"icon": "leaf.fill",  "text": "Water the plants when you're back"},
            {"icon": "book.fill",  "text": "Five minutes reading on the bench"},
        ],
    }
