"""GET /api/mood-wins/last-7 — 7-day mood × wins timeline for the home screen (T-34)"""

import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends

import db
from auth import get_current_user_id

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mood-wins", tags=["mood-wins"])

_MOOD_EMOJI = {
    "rough": "😔",
    "flat": "😐",
    "good": "🙂",
    "great": "😄",
}

# ISO weekday: Mon=0 … Sun=6
_DAY_LABEL = ["M", "T", "W", "T", "F", "S", "S"]


@router.get("/last-7")
async def get_mood_wins(user_id: str = Depends(get_current_user_id)):
    today = date.today()
    start = today - timedelta(days=6)

    subj_map, wins_map = await db.get_mood_wins_range(user_id, start, today)

    days_out = []
    for offset in range(6, -1, -1):
        d = today - timedelta(days=offset)
        subj = subj_map.get(d)
        label = _DAY_LABEL[d.weekday()]
        days_out.append({
            "date": d.isoformat(),
            "label": label,
            "mood_numeric": subj["mood_numeric"] if subj else None,
            "mood_emoji": _MOOD_EMOJI.get(subj["mood_label"]) if subj else None,
            "win_count": wins_map.get(d, 0),
        })

    # Prefer LLM-generated insight from today's brief, fall back to static copy.
    brief = await db.get_brief_today(user_id, today)
    insight = None
    if brief:
        insight = (brief.get("mood_wins_insight") or {}).get("headline")
    if not insight:
        insight = "Your strongest days had the most wins stacked under them."

    return {"days": days_out, "insight": insight}
