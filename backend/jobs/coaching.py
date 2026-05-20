"""
Sethko Coaching — daily brief composer (T-23)

Entry point: compose_brief(user_id, for_date) -> dict

Returns one of:
  { "coaching_card": { eyebrow, headline, body, action_label }, "metric_stamps": null }
  { "state": "still_learning" }   — outlook baseline < 14 days
  { "state": "needs_mode" }       — user.mode is null
"""

import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from typing import Optional

import anthropic

import db
from lib.outlook import compute_outlook
from models import RecoverySignal, User
import whoop as whoop_module
from jobs import oura_pull as oura_module

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

_PROMPT_PATH = Path(__file__).parent.parent / "lib" / "prompts" / "coaching.md"

TOOL_DEF = {
    "name": "generate_coaching_card",
    "description": "Output the coaching card for today's Daily Brief.",
    "input_schema": {
        "type": "object",
        "properties": {
            "coaching_card": {
                "type": "object",
                "properties": {
                    "eyebrow":      {"type": "string"},
                    "headline":     {"type": "string"},
                    "body":         {"type": "string"},
                    "action_label": {"type": "string"},
                },
                "required": ["eyebrow", "headline", "body", "action_label"],
            }
        },
        "required": ["coaching_card"],
    },
}

SAFE_PAYLOAD = {
    "coaching_card": {
        "eyebrow": "Good morning",
        "headline": "Your data is in — let's see what today holds.",
        "body": "Check back in a moment while we pull everything together.",
        "action_label": "See today's plan",
    },
    "metric_stamps": None,
}


def _load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _30d_baseline(signals: list[RecoverySignal]) -> dict:
    def _avg(vals):
        clean = [v for v in vals if v is not None]
        return round(mean(clean), 1) if clean else None

    return {
        "hrv_ms":       _avg([s.hrv_ms for s in signals]),
        "sleep_eff_pct": _avg([s.sleep_score for s in signals]),
        "rhr_bpm":      _avg([s.rhr_bpm for s in signals]),
        "strain":       _avg([s.strain_score for s in signals]),
    }


def _build_snapshot(
    user: User,
    today_signal: Optional[RecoverySignal],
    yesterday_subjective,
    signals_30d: list[RecoverySignal],
    outlook,
    recent_vice: bool,
) -> dict:
    yesterday_obj = None
    if today_signal:
        yesterday_obj = {
            "hrv_ms":       today_signal.hrv_ms,
            "sleep_h":      today_signal.sleep_hours,
            "sleep_eff_pct": today_signal.sleep_score,
            "rhr_bpm":      today_signal.rhr_bpm,
            "strain":       today_signal.strain_score,
        }

    return {
        "yesterday_subjective": {
            "mood":       yesterday_subjective.mood_label,
            "energy":     yesterday_subjective.energy,
            "motivation": yesterday_subjective.motivation,
            "clarity":    yesterday_subjective.clarity,
            "note":       yesterday_subjective.note,
        } if yesterday_subjective else None,
        "yesterday_objective":  yesterday_obj,
        "30d_baseline":         _30d_baseline(signals_30d),
        "today_outlook":        outlook.value if outlook else None,
        "today_outlook_tier":   outlook.tier  if outlook else None,
        "planned_activity":     None,    # T-27 will populate
        "recent_patterns":      [],      # T-30 will populate
        "user_mode":            user.mode,
        "prior_3_day_completion": {},    # T-20/T-29 will populate
        "recent_vice":          recent_vice,
    }


async def _call_claude(snapshot: dict, user: User) -> dict:
    system_prompt = _load_system_prompt()
    user_message = (
        f"User: {user.name} · modality: {user.dietary_modality} · goal: {user.goal} · mode: {user.mode}\n\n"
        "Data snapshot:\n"
        + json.dumps(snapshot, indent=2, default=str)
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=600,
        temperature=0.4,
        system=system_prompt,
        tools=[TOOL_DEF],
        tool_choice={"type": "tool", "name": "generate_coaching_card"},
        messages=[{"role": "user", "content": user_message}],
    )

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if not tool_use:
        raise ValueError("Claude returned no tool_use block")

    result = tool_use.input
    result["metric_stamps"] = None
    return result


async def compose_brief(user_id: str, for_date: date) -> dict:
    """
    Full pipeline: pull → load → compute → call Claude → persist → return.
    Safe to call repeatedly; persists to brief_today upsert.
    """
    user = await db.get_user(user_id)
    if not user:
        raise ValueError(f"User {user_id!r} not found")

    # T-24: mode must be chosen before we can generate.
    if user.mode is None:
        payload = {"state": "needs_mode"}
        await db.save_brief_today(user_id, for_date, payload)
        return payload

    # Device pull (best-effort). Branch on user.device; fall back to recovery_source for
    # existing users whose device column hasn't been set yet.
    active_device = user.device or (user.recovery_source if user.recovery_source != "both" else "whoop")
    try:
        if active_device == "oura":
            tokens = await db.get_oura_tokens(user_id)
            if tokens:
                tokens = oura_module.refresh_if_needed(tokens)
                await db.save_oura_tokens(user_id, tokens)
                signal = await oura_module.fetch_recent(tokens["access_token"])
                if signal is None:
                    # Ring not worn last night — Outlook falls back to "Still learning you".
                    log.info("Oura: no data for last night (ring not worn) user=%s", user_id)
                    payload = {"state": "missing_night"}
                    await db.save_brief_today(user_id, for_date, payload)
                    await db.update_sync_state(user_id, {"error": None})
                    return payload
                await db.upsert_recovery_signal(user_id, signal)
                await db.update_sync_state(user_id, {"error": None, "synced_at": signal.date.isoformat()})
        else:
            tokens = await db.get_whoop_tokens(user_id)
            if tokens:
                tokens = await whoop_module.refresh_if_needed(tokens)
                await db.save_whoop_tokens(user_id, tokens)
                signal = await whoop_module.fetch_recent(tokens["access_token"])
                await db.upsert_recovery_signal(user_id, signal)
    except Exception as exc:
        log.warning("%s pull failed for user=%s: %s", active_device, user_id, exc)
        if active_device == "oura":
            err = "needs_reauth" if "401" in str(exc) or "400" in str(exc) else str(exc)
            await db.update_sync_state(user_id, {"error": err})

    # Load DB state.
    today_signal     = await db.get_latest_recovery_signal(user_id)
    signals_30d      = await db.get_recovery_signals(user_id, days=30)
    yesterday        = for_date - timedelta(days=1)
    yesterday_subj   = await db.get_subjective_log(user_id, yesterday)
    recent_vice      = await db.had_recent_vice(user_id)

    # Compute outlook (requires ≥14 scored days).
    outlook = compute_outlook(sorted(signals_30d, key=lambda s: s.date))
    if outlook is None:
        payload = {"state": "still_learning"}
        await db.save_brief_today(user_id, for_date, payload)
        return payload

    snapshot = _build_snapshot(user, today_signal, yesterday_subj, signals_30d, outlook, recent_vice)

    try:
        payload = await _call_claude(snapshot, user)
    except Exception as exc:
        log.error("Claude call failed for user=%s: %s — using safe payload", user_id, exc)
        payload = SAFE_PAYLOAD.copy()

    await db.save_brief_today(user_id, for_date, payload)
    log.info("Brief composed for user=%s outlook=%s", user_id, outlook.value)
    return payload
