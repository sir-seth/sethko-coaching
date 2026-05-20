"""
Device abstraction for Sethko Coaching.

Field mapping (Oura v2 API  ↔  Whoop API  →  recovery_signal column):

  HRV:            Oura sleep.average_hrv (ms, RMSSD-based)         ↔  Whoop recovery.hrv_rmssd_milli            → hrv_ms
  Sleep duration: Oura sleep.total_sleep_duration (seconds)        ↔  Whoop (total_in_bed_time_milli - awake_ms) → sleep_hours
  Sleep score:    Oura sleep.efficiency (0–100)                    ↔  Whoop sleep.sleep_performance_percentage   → sleep_score
  RHR:            Oura sleep.lowest_heart_rate (bpm)               ↔  Whoop recovery.resting_heart_rate          → rhr_bpm
  Strain:         Oura daily_activity.score (0–100) rescaled       ↔  Whoop cycle.strain (0–21)                  → strain_score
  Recovery score: Oura daily_readiness.score (0–100)               ↔  Whoop recovery.recovery_score              → recovery_score

Strain rescale: oura_strain = (activity_score / 100) * 21
This is an approximation — Whoop strain is a Bayesian cardiovascular load model built from
heart-rate data across the full day; Oura's activity score is a composite readiness metric.
The pattern engine (T-30) must NOT surface "strain ↔ mood" correlations for Oura users because
the noise floor is too high. Flag any such correlation attempt in that module.
"""

from enum import Enum


class Device(str, Enum):
    whoop = "whoop"
    oura  = "oura"


def rescale_oura_strain(activity_score: float | None) -> float | None:
    """Map Oura activity score (0–100) to approximate Whoop-equivalent strain (0–21)."""
    if activity_score is None:
        return None
    return round((activity_score / 100.0) * 21.0, 2)
