"""
T-30 — pattern engine API routes.

GET /api/patterns/top
  Returns the single best qualifying finding, or insufficient_data state.

GET /api/patterns/drivers?response=recovery_signal.hrv_ms&window=30
  Returns up to 5 qualifying findings for a given response key, sorted by
  abs(effect) desc. Bar widths on the client are normalized to the largest
  abs(effect) in the returned set (not to r — restated from T-31 Gotchas).
"""

from fastapi import APIRouter, Depends, Query

import db
from auth import get_current_user_id
from lib.hypotheses import HYPOTHESES

router = APIRouter(prefix="/api/patterns", tags=["patterns"])

# Maps predictor column key → human-readable label for the drivers card.
_HUMAN_LABEL: dict[str, str] = {h.predictor: h.human_predictor for h in HYPOTHESES}


@router.get("/top")
async def patterns_top(user_id: str = Depends(get_current_user_id)):
    row = await db.get_patterns_top(user_id)
    if row is None:
        days = await db.get_patterns_days_observed(user_id)
        return {"state": "insufficient_data", "days_observed": days}
    return {**row, "human_label": _HUMAN_LABEL.get(row["predictor"], row["predictor"])}


@router.get("/drivers")
async def patterns_drivers(
    response: str = Query(default="recovery_signal.hrv_ms"),
    user_id: str = Depends(get_current_user_id),
):
    rows = await db.get_patterns_drivers(user_id, response)
    enriched = [{**r, "human_label": _HUMAN_LABEL.get(r["predictor"], r["predictor"])} for r in rows]
    return {"drivers": enriched, "response_key": response}
