"""
Outlook formula v1.

compute_outlook(signals) → Outlook | None

Inputs (all derived from RecoverySignal):
  HRV vs 30-day personal baseline  → z*30+50, clamped 0-100  (40% weight)
  Sleep score 0-100 as-is                                     (25% weight)
  RHR vs 30-day personal baseline  → -z*30+50, clamped 0-100 (15% weight)
  Prior-day strain modifier        → flat -5 or -10 deduction (20% weight)

Tier map (matches outlookFor() in tokens.jsx):
  ≥70 bright · 50-69 steady · 30-49 low · <30 rough
"""

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np

from models import RecoverySignal


@dataclass
class Outlook:
    value: int
    tier: Literal["bright", "steady", "low", "rough"]
    inputs: dict


def tier_for(value: int) -> Literal["bright", "steady", "low", "rough"]:
    if value >= 70:
        return "bright"
    if value >= 50:
        return "steady"
    if value >= 30:
        return "low"
    return "rough"


def _z(today_val: float, history: list[float], min_std: float = 1.0) -> float:
    """
    Z-score of today_val vs history.
    min_std floor prevents division by near-zero when baseline is unusually stable.
    Returns 0 on insufficient data.
    """
    if len(history) < 3:
        return 0.0
    arr = np.array(history, dtype=float)
    std = max(float(np.std(arr)), min_std)
    return float((today_val - float(np.mean(arr))) / std)


def compute_outlook(signals: list[RecoverySignal]) -> Optional[Outlook]:
    """
    signals: RecoverySignal list sorted ascending by date.
    The last entry is treated as today; all prior entries form the baseline.
    Returns None if fewer than 14 records exist (first-14-days rule from the brief).
    """
    if len(signals) < 14:
        return None

    today = signals[-1]
    history = signals[:-1]  # baseline excludes today

    # --- HRV component (40%) ---
    hrv_hist = [s.hrv_ms for s in history if s.hrv_ms is not None]
    if today.hrv_ms is not None and len(hrv_hist) >= 3:
        z_hrv = _z(today.hrv_ms, hrv_hist)
        hrv_score = float(np.clip(z_hrv * 30 + 50, 0, 100))
    else:
        z_hrv = None
        hrv_score = 50.0  # neutral when data is missing

    # --- Sleep component (25%) ---
    if today.sleep_score is not None:
        sleep_score = float(np.clip(today.sleep_score, 0, 100))
    else:
        sleep_score = 50.0

    # --- RHR component (15%) ---
    rhr_hist = [s.rhr_bpm for s in history if s.rhr_bpm is not None]
    if today.rhr_bpm is not None and len(rhr_hist) >= 3:
        z_rhr = _z(today.rhr_bpm, rhr_hist)
        rhr_score = float(np.clip(-z_rhr * 30 + 50, 0, 100))
    else:
        z_rhr = None
        rhr_score = 50.0

    # --- Prior-day strain modifier (20% as flat deduction) ---
    # signals[-2] is yesterday if it exists.
    yesterday = signals[-2] if len(signals) >= 2 else None
    yesterday_strain = yesterday.strain_score if yesterday else None
    if yesterday_strain is not None and yesterday_strain > 17:
        strain_penalty = 10
    elif yesterday_strain is not None and yesterday_strain > 14:
        strain_penalty = 5
    else:
        strain_penalty = 0

    # --- Weighted combination ---
    # The three scored components cover 80% of the weight; the strain slot's
    # neutral baseline contributes the remaining 10 points (50 * 0.20).
    # Subtracting strain_penalty from that base produces the flat deduction.
    raw = hrv_score * 0.40 + sleep_score * 0.25 + rhr_score * 0.15 + 50 * 0.20
    value = int(np.clip(round(raw - strain_penalty), 0, 100))

    return Outlook(
        value=value,
        tier=tier_for(value),
        inputs={
            "hrv_score": round(hrv_score, 1),
            "sleep_score": round(sleep_score, 1),
            "rhr_score": round(rhr_score, 1),
            "strain_penalty": strain_penalty,
            "z_hrv": round(z_hrv, 2) if z_hrv is not None else None,
            "z_rhr": round(z_rhr, 2) if z_rhr is not None else None,
            "hrv_ms_today": today.hrv_ms,
            "rhr_today": today.rhr_bpm,
            "sleep_score_today": today.sleep_score,
            "yesterday_strain": yesterday_strain,
            "n_signals": len(signals),
        },
    )
