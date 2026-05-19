"""
Sethko Coaching — coaching engine (backend module)

Absorbed from coach.py. Two responsibilities:
  1. build_digest() — assembles the structured data snapshot Claude receives
  2. generate_coaching() — calls Claude, validates and returns the JSON response

The prompt text (BASE_SYSTEM_PROMPT, MODALITY_BLOCKS, GOAL_BLOCKS) is
identical to coach.py. Changes here are the canonical version going forward.
"""

import json
import logging
from datetime import date
from statistics import mean
from typing import Optional

import anthropic

from models import RecoverySignal, User, WeightPoint

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

REQUIRED_FIELDS = {
    "recovery_read": ["headline", "detail"],
    "nutrition_target_today": ["kcal", "protein_g", "carbs_g", "fat_g", "rationale"],
    "weight_trend": ["headline", "detail"],
    "workout_suggestion": ["type", "strain_target", "detail"],
    "daily_brief": None,
}


# ---------------------------------------------------------------------------
# Digest builder
# ---------------------------------------------------------------------------

def build_digest(
    user: User,
    recovery: Optional[RecoverySignal],
    health: Optional[dict],
    recent_workouts: list[dict],
    weight_trend: list[WeightPoint],
) -> dict:
    """
    Assemble the compact data snapshot Claude reasons over.
    All None fields become explicit gaps — Claude is instructed not to
    fabricate numbers for missing data.
    """
    # Weight analysis.
    weight_today = health.get("weight_lbs") if health else None
    weight_7d_avg = None
    weight_30d_change = None

    if len(weight_trend) >= 2:
        recent_weights = [w.weight_lbs for w in weight_trend[-7:]]
        weight_7d_avg = round(mean(recent_weights), 1) if recent_weights else None
        weight_30d_change = round(
            weight_trend[-1].weight_lbs - weight_trend[0].weight_lbs, 1
        )

    # Workout summary for the prompt.
    workouts_summary = []
    for w in recent_workouts[:7]:
        workouts_summary.append({
            "date": w["started_at"].date().isoformat() if w.get("started_at") else None,
            "sport_name": w.get("sport_name"),
            "duration_min": w.get("duration_min"),
            "avg_hr_bpm": w.get("avg_hr_bpm"),
            "strain_score": w.get("strain_score"),
            "source": w.get("source"),
        })

    # Data gap reporting — Claude shouldn't invent numbers for missing fields.
    data_gaps = {}
    if not recovery:
        data_gaps["recovery"] = "No Whoop data available yet for today."
    if weight_today is None:
        data_gaps["body_weight"] = (
            "No weight reading from Apple Health yet today. "
            "If the user has a Bluetooth scale, it may not have synced yet."
        )
    if not recent_workouts:
        data_gaps["workouts"] = "No workout sessions found in the last 7 days."

    return {
        "user": {
            "name": user.name,
            "dietary_modality": user.dietary_modality,
            "goal": user.goal,
        },
        "today": {
            "date": date.today().isoformat(),
            "recovery_score": recovery.recovery_score if recovery else None,
            "hrv_ms": recovery.hrv_ms if recovery else None,
            "rhr_bpm": recovery.rhr_bpm if recovery else None,
            "sleep_hours": recovery.sleep_hours if recovery else None,
            "sleep_score_pct": recovery.sleep_score if recovery else None,
            "strain_score": recovery.strain_score if recovery else None,
            "weight_lbs": weight_today,
        },
        "trends": {
            "weight_7d_avg_lbs": weight_7d_avg,
            "weight_30d_change_lbs": weight_30d_change,
            "weight_readings_available": len(weight_trend),
        },
        "recent_workouts": workouts_summary,
        "data_gaps": data_gaps if data_gaps else None,
    }


# ---------------------------------------------------------------------------
# Claude call
# ---------------------------------------------------------------------------

BASE_SYSTEM_PROMPT = """\
You are a personal fitness coach with expertise in strength training, nutrition, and recovery science. You receive a daily data snapshot for a user and generate a concise, actionable coaching brief.

The user's profile (modality and goal) is described below. Read it carefully — it overrides any default coaching instincts. The same data shape goes in for every user; the rules and tone change based on this profile.

Your output MUST be a single JSON object with exactly these top-level fields and no others:

{
  "recovery_read": {
    "headline": "string, max 8 words",
    "detail": "string, 2-3 sentences referencing actual numbers"
  },
  "nutrition_target_today": {
    "kcal": number,
    "protein_g": number,
    "carbs_g": number,
    "fat_g": number,
    "rationale": "string, 1-2 sentences"
  },
  "weight_trend": {
    "headline": "string, max 8 words",
    "detail": "string, 1-2 sentences"
  },
  "workout_suggestion": {
    "type": "string (e.g. strength, cardio, rest, active recovery)",
    "strain_target": "string (e.g. 14-16)",
    "detail": "string, 1-2 sentences"
  },
  "daily_brief": "string, 3-4 sentences synthesizing all signals into one coaching message"
}

Universal rules:
- Be specific. Reference actual numbers from the data, not generic advice.
- If HRV is significantly above the user's recent baseline, say so and recommend pushing within what the modality allows.
- If HRV is significantly below baseline, say so and recommend recovery.
- Never give generic advice like "stay hydrated" without grounding it in the data.
- If a field appears in data_gaps, do not fabricate numbers for it. For nutrition_target_today, still set targets based on goal and estimated needs, but note in rationale that the relevant data is unavailable. For weight_trend, if weight data is missing, say so plainly in the headline.
- Output ONLY the JSON object. No preamble, no markdown fences, no closing remarks.
"""

MODALITY_BLOCKS = {
    "high_protein_performance": (
        "Modality: HIGH-PROTEIN PERFORMANCE. User is in serious strength or hypertrophy training. "
        "Protein floor is 0.8-1g per lb bodyweight, non-negotiable. On high-strain days, push protein "
        "toward the upper end and add carbs to support recovery. Tone: precise, performance-framed. "
        "Flag if protein is at risk of dropping below floor."
    ),
    "maintenance_active": (
        "Modality: MAINTENANCE + ACTIVE LIFESTYLE. User trains casually and wants consistency over precision. "
        "Protein floor is 0.6-0.8g per lb bodyweight. No aggressive deficit or surplus pushes — focus the "
        "brief on whether the user is showing up consistently. Flag only meaningful deviations from the user's "
        "recent pattern. Tone: encouraging, low-pressure, never demanding."
    ),
    "carb_cycling": (
        "Modality: CARB CYCLING. User cycles carbs based on training. On workout days, carbs are higher; "
        "on rest days, lower. Read today's recovery and recent workouts to set today's carb target. Protein "
        "stays high and constant."
    ),
    "low_carb_keto": (
        "Modality: LOW-CARB / KETO. Carbs under 50g/day, fat is the primary fuel. HRV baseline may run "
        "lower than general population — don't over-flag low HRV unless it's below the user's personal "
        "baseline. Suggest a refeed if performance metrics drop for several days."
    ),
    "glp1_supported": (
        "Modality: GLP-1 / LOW-CALORIE SUPPORTED. Appetite is suppressed; primary risk is muscle loss. "
        "Calorie targets are low but protein targets stay high (0.7-0.9g per lb bodyweight). "
        "Flag firmly if protein drops below floor for 2+ days. Tone: protective, muscle-preservation-focused."
    ),
    "flexible": (
        "Modality: FLEXIBLE. User has not committed to a framework. Observe patterns and adapt. "
        "Be honest when data is insufficient to make strong recommendations. Don't impose structure."
    ),
}

GOAL_BLOCKS = {
    "cut": (
        "Goal: CUT. Calorie deficit, preserve muscle. Set kcal ~500 below estimated maintenance. "
        "Protein stays high. Recovery dips are expected on a cut — don't over-react unless HRV is "
        "consistently below baseline."
    ),
    "recomp": (
        "Goal: RECOMP. Small deficit or maintenance with high protein and progressive training. "
        "Weight changes will be slow — focus weight_trend on multi-week direction, not daily fluctuations."
    ),
    "bulk": (
        "Goal: BULK. Calorie surplus ~300-500 above estimated maintenance. Protein high. Some weight gain "
        "is expected and good. Flag if gaining >1lb/week sustained — likely excess fat gain."
    ),
    "maintain": (
        "Goal: MAINTAIN. Targets at estimated maintenance. Focus the brief on performance and recovery, "
        "not on the scale."
    ),
    "performance": (
        "Goal: PERFORMANCE. Weight is secondary; optimize for training output and recovery. Targets "
        "support today's planned strain. Flag under-fueling around hard sessions."
    ),
}


def _compose_system_prompt(modality: str, goal: str) -> str:
    modality_block = MODALITY_BLOCKS.get(modality, MODALITY_BLOCKS["flexible"])
    goal_block = GOAL_BLOCKS.get(goal, GOAL_BLOCKS["maintain"])
    return (
        BASE_SYSTEM_PROMPT
        + "\n\nUser profile:\n"
        + modality_block
        + "\n\n"
        + goal_block
    )


def _validate(parsed: dict):
    errors = []
    for top_key, sub_keys in REQUIRED_FIELDS.items():
        if top_key not in parsed:
            errors.append(f"missing: {top_key}")
            continue
        if sub_keys is None:
            if not isinstance(parsed[top_key], str):
                errors.append(f"{top_key} must be a string")
            continue
        if not isinstance(parsed[top_key], dict):
            errors.append(f"{top_key} must be an object")
            continue
        for sub in sub_keys:
            if sub not in parsed[top_key]:
                errors.append(f"missing: {top_key}.{sub}")
    if errors:
        raise ValueError("Claude response failed schema validation: " + "; ".join(errors))


async def generate_coaching(digest: dict, modality: str, goal: str) -> dict:
    """
    Call Claude with the digest and return the parsed coaching JSON.
    Raises on API error or schema validation failure.
    """
    system_prompt = _compose_system_prompt(modality, goal)
    user_message = (
        "Here is the user's data snapshot. Generate the coaching JSON.\n\n"
        + json.dumps(digest, indent=2, default=str)
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = "".join(b.text for b in response.content if b.type == "text").strip()

    # Strip code fences if Claude ignores the instruction.
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()

    parsed = json.loads(raw)
    _validate(parsed)

    log.info(
        "Coaching generated. Tokens: %d in / %d out. Cost: ~$%.4f",
        response.usage.input_tokens,
        response.usage.output_tokens,
        (response.usage.input_tokens / 1_000_000 * 3)
        + (response.usage.output_tokens / 1_000_000 * 15),
    )

    return parsed
