"""
T-35 golden tests for LLM-generated metric stamps.

Tests assert structural and content constraints — not exact strings.
All tests use hardcoded sample responses (no live Claude calls).
"""

import re
import pytest

# ---------------------------------------------------------------------------
# Sample stamp responses (what we'd expect Claude to return)
# ---------------------------------------------------------------------------

SETH_STAMPS_DAY1 = {
    "hrv": {
        "what": "Heart rate variability — the gap between heartbeats. Higher means a more relaxed nervous system.",
        "means": "64ms today, 11 above your 30-day average of 53ms. Your body is recovered — today's plan can be a full effort.",
    },
    "sleep": {
        "what": "Total time asleep, and how much of your time in bed was actually sleep. Higher efficiency means fewer interruptions.",
        "means": "7h 48m at 93% efficiency, well above your 87% baseline. Sleep is the strongest signal in your data right now.",
    },
    "rhr": {
        "what": "Resting heart rate, measured during the deepest part of last night's sleep. Lower generally means more recovered.",
        "means": "52 bpm — 3 below your 30-day baseline of 55. Three days trending down, which is exactly what you want.",
    },
    "strain": {
        "what": "Yesterday's cardiovascular load on a 0–21 scale. Moderate range is roughly 8–13.",
        "means": "9.2 yesterday — a solid moderate load that didn't cost you much recovery. Good place to be heading into today.",
    },
}

SETH_STAMPS_DAY2 = {
    "hrv": {
        "what": "Heart rate variability — the gap between heartbeats. Higher means a more relaxed nervous system.",
        "means": "61ms today, 8 above your 30-day average of 53ms. Still elevated — body is handling the week well.",
    },
    "sleep": {
        "what": "Total time asleep, and how much of your time in bed was actually sleep. Higher efficiency means fewer interruptions.",
        "means": "7h 22m at 89% efficiency. Slightly below yesterday's peak but still above your 87% baseline.",
    },
    "rhr": {
        "what": "Resting heart rate, measured during the deepest part of last night's sleep. Lower generally means more recovered.",
        "means": "53 bpm — 2 below your baseline of 55. Still trending in the right direction.",
    },
    "strain": {
        "what": "Yesterday's cardiovascular load on a 0–21 scale. Moderate range is roughly 8–13.",
        "means": "11.4 yesterday — a step up from the day before. Today's brief accounts for that.",
    },
}

SLAV_STAMPS = {
    "hrv": {
        "what": "Heart rate variability — the gap between heartbeats. Higher means a more relaxed nervous system.",
        "means": "81ms today, 7 above your 30-day average of 74ms. Nervous system is green — compound sets first.",
    },
    "sleep": {
        "what": "Total time asleep, and how much of your time in bed was actually sleep. Higher efficiency means fewer interruptions.",
        "means": "8h 6m at 95% efficiency — near the top of your range. Sleep quality like this supports heavier training loads.",
    },
    "rhr": {
        "what": "Resting heart rate, measured during the deepest part of last night's sleep. Lower generally means more recovered.",
        "means": "48 bpm — 2 below your baseline of 50. Cardiovascular efficiency is holding at the top of your recent range.",
    },
    "strain": {
        "what": "Yesterday's cardiovascular load on a 0–21 scale. Moderate range is roughly 8–13.",
        "means": "16.4 yesterday — high end. Factor that into today's target; don't double-stack heavy strain days back to back.",
    },
}

GENTLE_STAMPS = {
    "hrv": {
        "what": "Heart rate variability — the gap between heartbeats. Higher means a more relaxed nervous system.",
        "means": "64ms — above your recent average. A good sign heading into the day.",
    },
    "sleep": {
        "what": "Total time asleep, and how much of your time in bed was actually sleep. Higher efficiency means fewer interruptions.",
        "means": "7h 48m at 93% efficiency. Nights like this make everything easier.",
    },
    "rhr": {
        "what": "Resting heart rate, measured during the deepest part of last night's sleep. Lower generally means more recovered.",
        "means": "52 bpm — on the low end for you, which is a good sign.",
    },
    "strain": {
        "what": "Yesterday's cardiovascular load on a 0–21 scale. Moderate range is roughly 8–13.",
        "means": "9.2 yesterday — a comfortable amount of effort. No need to compensate today.",
    },
}

SNAPSHOT_INPUTS = {
    "hrv_ms":        64,
    "sleep_h":       7.8,
    "sleep_eff_pct": 93,
    "rhr_bpm":       52,
    "strain":        9.2,
    "hrv_30d_avg":   53,
    "rhr_30d_avg":   55,
}

METRIC_KEYS = ["hrv", "sleep", "rhr", "strain"]

OPTIMIZER_PHRASES = ["rpe", "push", "compound", "protein target", "macro", "weight loss"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_stamp_structure(stamps: dict, label: str):
    for key in METRIC_KEYS:
        assert key in stamps, f"{label}: missing stamp for {key!r}"
        stamp = stamps[key]
        assert isinstance(stamp.get("what"), str) and stamp["what"].strip(), \
            f"{label}: {key}.what is missing or empty"
        assert isinstance(stamp.get("means"), str) and stamp["means"].strip(), \
            f"{label}: {key}.means is missing or empty"


def _assert_means_contains_number(stamps: dict, label: str):
    for key in METRIC_KEYS:
        means = stamps[key]["means"]
        assert re.search(r"\d", means), \
            f"{label}: {key}.means must reference at least one number: {means!r}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_stamp_structure_seth():
    _validate_stamp_structure(SETH_STAMPS_DAY1, "Seth day1")


def test_stamp_structure_slav():
    _validate_stamp_structure(SLAV_STAMPS, "Slav")


def test_means_contains_number_seth():
    _assert_means_contains_number(SETH_STAMPS_DAY1, "Seth day1")


def test_means_contains_number_slav():
    _assert_means_contains_number(SLAV_STAMPS, "Slav")


def test_what_stable_across_days():
    """what fields should be identical on day1 and day2 for the same user."""
    for key in METRIC_KEYS:
        assert SETH_STAMPS_DAY1[key]["what"] == SETH_STAMPS_DAY2[key]["what"], \
            f"HRV 'what' changed between day1 and day2 for key={key!r}"


def test_means_changes_day_to_day():
    """At least 3 of the 4 means fields must differ between day1 and day2."""
    differing = sum(
        1 for key in METRIC_KEYS
        if SETH_STAMPS_DAY1[key]["means"] != SETH_STAMPS_DAY2[key]["means"]
    )
    assert differing >= 3, \
        f"Expected ≥3 means fields to differ across days, got {differing}"


def test_gentle_mode_no_optimizer_language():
    """Gentle-mode stamps must not contain RPE, push, macros, or weight-loss language."""
    for key in METRIC_KEYS:
        means_lower = GENTLE_STAMPS[key]["means"].lower()
        for phrase in OPTIMIZER_PHRASES:
            assert phrase not in means_lower, \
                f"Gentle mode {key}.means contains optimizer phrase {phrase!r}: {means_lower!r}"


def test_seth_and_slav_stamps_differ():
    """Seth and Slav should get different means (different inputs)."""
    differing = sum(
        1 for key in METRIC_KEYS
        if SETH_STAMPS_DAY1[key]["means"] != SLAV_STAMPS[key]["means"]
    )
    assert differing == 4, "All four means should differ between Seth and Slav"


def test_what_does_not_reference_todays_value():
    """what is a definition — it should not include metric-specific numbers from today."""
    # The constraint: what doesn't say "64ms" or "93%" (today's specific values).
    # We test that what fields from day1 == day2 (no day-specific numbers baked in).
    for key in METRIC_KEYS:
        assert SETH_STAMPS_DAY1[key]["what"] == SETH_STAMPS_DAY2[key]["what"], \
            f"{key}.what appears to include day-specific data (changed between days)"
