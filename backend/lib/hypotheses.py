"""
T-30 — closed hypothesis registry for the pattern engine.

Each Hypothesis names a (predictor, response, lag) triple. The engine tests
only these 22 pairs — open-ended discovery is explicitly out of scope so
Holm-Bonferroni correction stays meaningful.

kind:
  'continuous' — Spearman rank correlation
  'ordinal'    — Spearman rank correlation (mood/energy are ordinal scales)
  'boolean'    — point-biserial correlation
"""

from dataclasses import dataclass

# Qualifying thresholds — defined here so tests can import them.
MIN_N: int = 14
MIN_ABS_R: float = 0.5
MAX_P: float = 0.05


@dataclass(frozen=True)
class Hypothesis:
    predictor: str       # DataFrame column key for the predictor
    response: str        # DataFrame column key for the response
    lag_days: int        # predictor on day d paired with response on day d+lag
    kind: str            # 'continuous' | 'ordinal' | 'boolean'
    human_predictor: str # natural-language label for LLM prompt
    human_response: str  # natural-language label for LLM prompt
    effect_unit: str     # unit for the effect size (ms, bpm, %, pts, count)


HYPOTHESES: list[Hypothesis] = [
    # Subjective → next-morning HRV (the thesis-encoded direction)
    Hypothesis("subjective.mood",       "recovery_signal.hrv_ms",        1, "ordinal",    "morning mood",           "next-morning HRV",      "ms"),
    Hypothesis("subjective.energy",     "recovery_signal.hrv_ms",        1, "ordinal",    "energy level",           "next-morning HRV",      "ms"),
    Hypothesis("subjective.motivation", "workout_log.completed_count",   0, "ordinal",    "motivation level",       "workout completion",    "count"),
    Hypothesis("subjective.clarity",    "recovery_signal.hrv_ms",        1, "ordinal",    "mental clarity",         "next-morning HRV",      "ms"),

    # Sleep → HRV and subjective
    Hypothesis("recovery_signal.sleep_h",        "recovery_signal.hrv_ms",   0, "continuous", "sleep duration",         "HRV",                   "ms"),
    Hypothesis("recovery_signal.sleep_eff_pct",  "recovery_signal.hrv_ms",   0, "continuous", "sleep efficiency",       "HRV",                   "ms"),
    Hypothesis("recovery_signal.sleep_h",        "subjective.energy",         1, "continuous", "sleep duration",         "next-morning energy",   "pts"),

    # Strain → next-morning HRV
    Hypothesis("recovery_signal.strain", "recovery_signal.hrv_ms", 1, "continuous", "yesterday's strain",  "next-morning HRV", "ms"),

    # Resting HR → mood
    Hypothesis("recovery_signal.rhr_bpm", "subjective.mood", 0, "continuous", "resting heart rate", "same-day mood", "mood_pts"),

    # Nutrition → next-morning HRV / clarity
    Hypothesis("nutrition.protein_g",      "recovery_signal.hrv_ms",  1, "continuous", "protein intake",    "next-morning HRV",      "ms"),
    Hypothesis("nutrition.kcal_total",     "recovery_signal.hrv_ms",  1, "continuous", "total calories",    "next-morning HRV",      "ms"),
    Hypothesis("nutrition.fiber_g",        "subjective.clarity",       1, "continuous", "fiber intake",      "next-morning clarity",  "pts"),
    Hypothesis("nutrition.caffeine_late_mg","recovery_signal.sleep_eff_pct", 0, "continuous", "late caffeine", "sleep efficiency",  "%"),
    Hypothesis("nutrition.alcohol_units",  "recovery_signal.hrv_ms",  1, "continuous", "alcohol",           "next-morning HRV",      "ms"),
    Hypothesis("nutrition.alcohol_units",  "subjective.mood",          1, "continuous", "alcohol",           "next-morning mood",     "mood_pts"),

    # Vice log (boolean timestamp only — no category, no count)
    Hypothesis("vice_log_bool", "recovery_signal.hrv_ms", 1, "boolean", "vice logged", "next-morning HRV", "ms"),

    # Movement wins (boolean per day)
    Hypothesis("wins.walk_bool",     "subjective.mood",      1, "boolean", "walk day",       "next-morning mood",    "mood_pts"),
    Hypothesis("wins.meditate_bool", "subjective.clarity",   0, "boolean", "meditation day", "same-day clarity",     "pts"),
    Hypothesis("wins.lift_bool",     "recovery_signal.hrv_ms", 1, "boolean", "lifting day",  "next-morning HRV",     "ms"),

    # Training load and body weight
    Hypothesis("workout_log.strain_avg_7d", "subjective.energy", 0, "continuous", "7-day training load", "energy",  "pts"),
    Hypothesis("weight_lb_delta_7d",        "subjective.mood",   0, "continuous", "7-day weight change", "mood",    "mood_pts"),

    # Bedtime regularity
    Hypothesis("sleep_bedtime_hour", "recovery_signal.sleep_eff_pct", 0, "continuous", "bedtime (local hour)", "sleep efficiency", "%"),
]
