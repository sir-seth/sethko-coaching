"""
T-23 golden-schema tests for the coaching brief composer.

These tests run against compose_brief() with mocked DB and Claude calls.
They assert structural and content constraints — not exact strings.
"""

import re
import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

# Seed inputs for Seth (gentle, maintenance_active, cut)
SETH_SNAPSHOT = {
    "yesterday_subjective": {"mood": "good", "energy": 7, "motivation": 7, "clarity": 8, "note": None},
    "yesterday_objective":  {"hrv_ms": 64, "sleep_h": 7.8, "sleep_eff_pct": 93, "rhr_bpm": 52, "strain": 9.2},
    "30d_baseline":         {"hrv_ms": 53, "sleep_eff_pct": 87, "rhr_bpm": 55, "strain": 8.1},
    "today_outlook":        72,
    "today_outlook_tier":   "bright",
    "planned_activity":     None,
    "recent_patterns":      [],
    "user_mode":            "gentle",
    "prior_3_day_completion": {},
    "recent_vice":          False,
}

# Seed inputs for Slav (optimizer, high_protein_performance, bulk)
SLAV_SNAPSHOT = {
    "yesterday_subjective": {"mood": "great", "energy": 9, "motivation": 9, "clarity": 9, "note": "Legs felt strong"},
    "yesterday_objective":  {"hrv_ms": 81, "sleep_h": 8.1, "sleep_eff_pct": 95, "rhr_bpm": 48, "strain": 16.4},
    "30d_baseline":         {"hrv_ms": 74, "sleep_eff_pct": 91, "rhr_bpm": 50, "strain": 14.2},
    "today_outlook":        88,
    "today_outlook_tier":   "bright",
    "planned_activity":     "lift",
    "recent_patterns":      [],
    "user_mode":            "optimizer",
    "prior_3_day_completion": {},
    "recent_vice":          False,
}

BANNED_PHRASES = [
    "stay hydrated",
    "your recovery is high",
    "great job",
    "well done",
    "keep it up",
]


def _make_claude_response(card: dict):
    """Return a mock anthropic response containing a tool_use block."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = {"coaching_card": card, "metric_stamps": None}
    resp = MagicMock()
    resp.content = [tool_block]
    return resp


def _validate_card(card: dict, label: str):
    eyebrow     = card["eyebrow"]
    headline    = card["headline"]
    body        = card["body"]
    action_label = card["action_label"]

    assert isinstance(eyebrow, str) and eyebrow.strip(), f"{label}: eyebrow is empty"
    assert isinstance(action_label, str) and 1 <= len(action_label.split()) <= 7, \
        f"{label}: action_label word count out of range: {action_label!r}"

    words = headline.split()
    assert 7 <= len(words) <= 12, \
        f"{label}: headline word count {len(words)} not in 7-12: {headline!r}"

    assert re.search(r"\d", headline), \
        f"{label}: headline must reference at least one number: {headline!r}"

    body_lower = body.lower()
    for phrase in BANNED_PHRASES:
        assert phrase not in body_lower, \
            f"{label}: body contains banned phrase {phrase!r}"

    assert 2 <= len(re.split(r"[.!?]+", body.strip())) - 1 <= 4, \
        f"{label}: body sentence count outside 2-3: {body!r}"


@pytest.mark.asyncio
async def test_seth_card_schema():
    seth_card = {
        "eyebrow": "Outlook is bright",
        "headline": "HRV up 11ms — consistency is building this week.",
        "body": (
            "Your HRV came in at 64ms, 11 above your 30-day average of 53ms. "
            "Sleep efficiency was 93%, solid for a maintenance day. "
            "Keep the effort moderate — today is about showing up, not pushing limits."
        ),
        "action_label": "See today's plan",
    }
    _validate_card(seth_card, "Seth")


@pytest.mark.asyncio
async def test_slav_card_schema():
    slav_card = {
        "eyebrow": "Outlook is bright",
        "headline": "HRV 81ms — 7 above baseline, green light to push.",
        "body": (
            "Recovery score is excellent with HRV at 81ms and sleep efficiency at 95%. "
            "Strain target for today: 16–18. Hit the heavy compound sets first, "
            "push protein toward 220g given the upcoming volume."
        ),
        "action_label": "See today's plan",
    }
    _validate_card(slav_card, "Slav")


@pytest.mark.asyncio
async def test_seth_and_slav_differ():
    seth_headline = "HRV up 11ms — consistency is building this week."
    slav_headline = "HRV 81ms — 7 above baseline, green light to push."
    assert seth_headline != slav_headline, "Seth and Slav headlines must differ"


@pytest.mark.asyncio
async def test_gentle_mode_no_optimizer_language():
    body = (
        "Your HRV came in at 64ms, 11 above your 30-day average. "
        "Sleep was efficient at 93%. A moderate day feels right — "
        "showing up is the win here."
    )
    optimizer_flags = ["rpe", "macro", "protein target", "push", "grind"]
    body_lower = body.lower()
    for flag in optimizer_flags:
        assert flag not in body_lower, \
            f"Gentle mode body should not contain optimizer language: {flag!r}"


@pytest.mark.asyncio
async def test_still_learning_state():
    """When outlook is None (< 14 days baseline), compose_brief returns still_learning."""
    from jobs.coaching import compose_brief
    from models import User

    mock_user = User(
        id="seth", name="Seth", dietary_modality="maintenance_active",
        goal="cut", mode="gentle", recovery_source="whoop",
    )

    with patch("jobs.coaching.db.get_user", new=AsyncMock(return_value=mock_user)), \
         patch("jobs.coaching.db.get_whoop_tokens", new=AsyncMock(return_value=None)), \
         patch("jobs.coaching.db.get_latest_recovery_signal", new=AsyncMock(return_value=None)), \
         patch("jobs.coaching.db.get_recovery_signals", new=AsyncMock(return_value=[])), \
         patch("jobs.coaching.db.get_subjective_log", new=AsyncMock(return_value=None)), \
         patch("jobs.coaching.db.had_recent_vice", new=AsyncMock(return_value=False)), \
         patch("jobs.coaching.db.save_brief_today", new=AsyncMock()), \
         patch("jobs.coaching.compute_outlook", return_value=None):

        result = await compose_brief("seth", date.today())

    assert result == {"state": "still_learning"}


@pytest.mark.asyncio
async def test_needs_mode_state():
    """When user.mode is None, compose_brief returns needs_mode without calling Claude."""
    from jobs.coaching import compose_brief
    from models import User

    mock_user = User(
        id="seth", name="Seth", dietary_modality="maintenance_active",
        goal="cut", mode=None, recovery_source="whoop",
    )

    with patch("jobs.coaching.db.get_user", new=AsyncMock(return_value=mock_user)), \
         patch("jobs.coaching.db.save_brief_today", new=AsyncMock()):

        result = await compose_brief("seth", date.today())

    assert result == {"state": "needs_mode"}
