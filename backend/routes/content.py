"""
GET /api/content/exercise/{name}   → { youtube_id, caption, duration_s } or 404
GET /api/content/ambient           → list of tracks filtered by ?context=meditate|lift
GET /api/content/walks/saved       → user's saved walk destinations (empty in v1)

All responses are static YAML served from lib/content/. No DB writes here.
YAML is repo-controlled and human-curated; see T-42 acceptance criteria.
"""

import logging
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user_id

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/content", tags=["content"])

_CONTENT_DIR = Path(__file__).parent.parent / "lib" / "content"


def _load(filename: str):
    path = _CONTENT_DIR / filename
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        log.warning("Content file not found: %s", filename)
        return None


def _canonicalize(name: str) -> str:
    """'Bent-over row' → 'bent_over_row'"""
    return name.lower().replace(" ", "_").replace("-", "_").replace("+", "_")


@router.get("/exercise/{name}")
async def get_exercise_content(
    name: str,
    user_id: str = Depends(get_current_user_id),
):
    exercises = _load("exercises.yaml") or {}
    key = _canonicalize(name)
    entry = exercises.get(key)
    if not entry or entry.get("youtube_id") == "PLACEHOLDER":
        raise HTTPException(status_code=404, detail="No video for this exercise")
    return entry


@router.get("/ambient")
async def get_ambient_tracks(
    context: str = "meditate",
    user_id: str = Depends(get_current_user_id),
):
    tracks = _load("ambient_tracks.yaml") or []
    return [t for t in tracks if context in t.get("contexts", [])]


@router.get("/walks/saved")
async def get_saved_walks(user_id: str = Depends(get_current_user_id)):
    walks = _load("walks.yaml") or []
    return [w for w in walks if isinstance(w, dict) and w.get("user_id") == user_id]
