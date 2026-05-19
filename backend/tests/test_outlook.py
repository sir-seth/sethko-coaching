"""Unit tests for lib/outlook.py — no DB, no network."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date, timedelta

import pytest

from lib.outlook import compute_outlook, tier_for
from models import RecoverySignal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sig(
    hrv=60.0,
    rhr=55.0,
    sleep=70.0,
    strain=None,
    days_ago=0,
) -> RecoverySignal:
    return RecoverySignal(
        date=date.today() - timedelta(days=days_ago),
        source="whoop",
        hrv_ms=hrv,
        rhr_bpm=rhr,
        sleep_score=sleep,
        strain_score=strain,
    )


def _baseline(n=29, hrv=60.0, rhr=55.0, sleep=70.0, strain=10.0) -> list[RecoverySignal]:
    """n historical days with stable values, oldest first."""
    return [_sig(hrv=hrv, rhr=rhr, sleep=sleep, strain=strain, days_ago=n - i) for i in range(n)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_too_little_data_returns_none():
    signals = _baseline(n=13)
    assert compute_outlook(signals) is None


def test_exactly_14_days_returns_result():
    signals = _baseline(n=13) + [_sig(days_ago=0)]
    result = compute_outlook(signals)
    assert result is not None


def test_all_baseline_scores_near_50():
    """When today matches the historical mean exactly, score should be ~50."""
    signals = _baseline(n=29) + [_sig(days_ago=0)]
    result = compute_outlook(signals)
    assert result is not None
    assert 45 <= result.value <= 55
    assert result.tier == "steady"


def test_great_night_scores_bright():
    """HRV well above baseline, low RHR, high sleep → ≥70."""
    # Vary the baseline so std > 0 (mimics real data; flat baseline gives z=0)
    base = [
        _sig(hrv=60.0 + (i % 5) * 1.5, rhr=58.0 + (i % 3), sleep=68.0, strain=10.0, days_ago=29 - i)
        for i in range(29)
    ]
    today = _sig(hrv=85.0, rhr=50.0, sleep=92.0, days_ago=0)
    result = compute_outlook(base + [today])
    assert result is not None
    assert result.value >= 70
    assert result.tier == "bright"


def test_bad_night_scores_below_50():
    """HRV well below baseline, elevated RHR, poor sleep → <50."""
    base = _baseline(n=29, hrv=65.0, rhr=52.0, sleep=75.0)
    today = _sig(hrv=38.0, rhr=70.0, sleep=28.0, days_ago=0)
    result = compute_outlook(base + [today])
    assert result is not None
    assert result.value < 50


def test_high_prior_strain_depresses_score():
    """Same objective metrics; yesterday strain >17 should lower score by 10."""
    base = _baseline(n=28, hrv=60.0, rhr=55.0, sleep=70.0, strain=10.0)

    yesterday_light  = _sig(hrv=60.0, rhr=55.0, sleep=70.0, strain=10.0, days_ago=1)
    yesterday_heavy  = _sig(hrv=60.0, rhr=55.0, sleep=70.0, strain=18.0, days_ago=1)
    today            = _sig(hrv=60.0, rhr=55.0, sleep=70.0, days_ago=0)

    result_light = compute_outlook(base + [yesterday_light, today])
    result_heavy = compute_outlook(base + [yesterday_heavy, today])

    assert result_light is not None and result_heavy is not None
    assert result_heavy.value == result_light.value - 10


def test_moderate_prior_strain_subtracts_5():
    base = _baseline(n=28, hrv=60.0, rhr=55.0, sleep=70.0, strain=10.0)
    yesterday_mod  = _sig(hrv=60.0, rhr=55.0, sleep=70.0, strain=15.0, days_ago=1)
    yesterday_none = _sig(hrv=60.0, rhr=55.0, sleep=70.0, strain=10.0, days_ago=1)
    today          = _sig(hrv=60.0, rhr=55.0, sleep=70.0, days_ago=0)

    result_mod  = compute_outlook(base + [yesterday_mod,  today])
    result_none = compute_outlook(base + [yesterday_none, today])

    assert result_mod.value == result_none.value - 5


def test_tier_for_cutoffs():
    assert tier_for(70) == "bright"
    assert tier_for(69) == "steady"
    assert tier_for(50) == "steady"
    assert tier_for(49) == "low"
    assert tier_for(30) == "low"
    assert tier_for(29) == "rough"
    assert tier_for(0)  == "rough"


def test_missing_hrv_falls_back_to_neutral():
    """If HRV data is absent, HRV component defaults to 50 (no bias)."""
    signals = _baseline(n=29) + [_sig(hrv=None, rhr=55.0, sleep=70.0, days_ago=0)]
    result = compute_outlook(signals)
    assert result is not None
    assert result.inputs["hrv_score"] == 50.0
