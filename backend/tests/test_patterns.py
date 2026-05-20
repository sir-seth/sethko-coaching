"""
T-30 — unit tests for the pattern engine.

No DB, no network. All tests run against synthetic DataFrames built from
JSON fixtures or generated inline. The LLM copy step is not exercised —
that's an integration test.
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.hypotheses import HYPOTHESES, MIN_N, MIN_ABS_R, MAX_P
from lib.stats import spearman, point_biserial, holm_bonferroni, mean_delta_by_group, tercile_delta
from jobs.patterns import _compute_hypothesis, WINDOW_DAYS

FIXTURE_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixture(name: str) -> pd.DataFrame:
    """Convert a JSON fixture into a date-indexed DataFrame."""
    with open(FIXTURE_DIR / name) as f:
        records = json.load(f)
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.set_index("date").astype(float, errors="ignore")
    return df


def _random_df(seed: int, n: int = 28) -> pd.DataFrame:
    """28 days of fully random data — no genuine correlations."""
    rng = np.random.default_rng(seed)
    start = date(2025, 1, 1)
    idx = [start + timedelta(days=i) for i in range(n)]
    return pd.DataFrame({
        "subjective.mood":            rng.choice([2, 4, 7, 9], size=n).astype(float),
        "subjective.energy":          rng.integers(1, 11, size=n).astype(float),
        "subjective.motivation":      rng.integers(1, 11, size=n).astype(float),
        "subjective.clarity":         rng.integers(1, 11, size=n).astype(float),
        "recovery_signal.hrv_ms":     rng.uniform(35, 80, size=n),
        "recovery_signal.rhr_bpm":    rng.uniform(48, 70, size=n),
        "recovery_signal.sleep_h":    rng.uniform(5, 9, size=n),
        "recovery_signal.sleep_eff_pct": rng.uniform(60, 95, size=n),
        "recovery_signal.strain":     rng.uniform(5, 20, size=n),
        "nutrition.protein_g":        rng.uniform(80, 180, size=n),
        "nutrition.kcal_total":       rng.uniform(1500, 2800, size=n),
        "nutrition.fiber_g":          np.full(n, np.nan),
        "nutrition.caffeine_late_mg": np.full(n, np.nan),
        "nutrition.alcohol_units":    np.full(n, np.nan),
        "vice_log_bool":              rng.choice([0.0, 1.0], p=[0.85, 0.15], size=n),
        "wins.walk_bool":             rng.choice([0.0, 1.0], size=n).astype(float),
        "wins.meditate_bool":         rng.choice([0.0, 1.0], p=[0.7, 0.3], size=n).astype(float),
        "wins.lift_bool":             rng.choice([0.0, 1.0], p=[0.65, 0.35], size=n).astype(float),
        "workout_log.completed_count": rng.choice([0.0, 1.0], size=n).astype(float),
        "workout_log.strain_avg_7d":  rng.uniform(5, 18, size=n),
        "weight_lb_delta_7d":         rng.uniform(-2, 2, size=n),
        "sleep_bedtime_hour":         np.full(n, np.nan),
    }, index=idx)


def _run_all_hypotheses(df: pd.DataFrame) -> list[dict]:
    """Compute all 22 hypotheses, apply Holm correction, set qualifies."""
    from lib.stats import holm_bonferroni
    raw = [_compute_hypothesis(df, h) for h in HYPOTHESES]
    adj = holm_bonferroni([f["p_raw"] for f in raw])
    for i, f in enumerate(raw):
        f["p"] = adj[i]
        f["qualifies"] = f["n"] >= MIN_N and abs(f["r"]) >= MIN_ABS_R and f["p"] < MAX_P
    return raw


# ---------------------------------------------------------------------------
# stats.py unit tests
# ---------------------------------------------------------------------------

def test_spearman_perfect_positive():
    x = list(range(20))
    y = [v * 2.5 + 1 for v in x]
    r, p, n = spearman(x, y)
    assert abs(r - 1.0) < 1e-9
    assert p < 0.001
    assert n == 20


def test_spearman_rank_permutation_invariant():
    """Permuting the ranks should not change the statistic."""
    import random as rnd
    rnd.seed(7)
    x = [rnd.gauss(0, 1) for _ in range(30)]
    y = [v + rnd.gauss(0, 0.5) for v in x]
    r1, _, _ = spearman(x, y)

    # Apply a monotone transformation that preserves rank order
    x_perm = [v ** 3 for v in x]
    r2, _, _ = spearman(x_perm, y)
    assert abs(r1 - r2) < 1e-9


def test_spearman_too_few_points():
    r, p, n = spearman([1, 2], [3, 4])
    assert r == 0.0
    assert p == 1.0
    assert n == 2


def test_point_biserial_basic():
    x = [0] * 15 + [1] * 15
    y = [40.0] * 15 + [55.0] * 15
    r, p, n = point_biserial(x, y)
    assert r > 0.8
    assert p < 0.001
    assert n == 30


def test_holm_bonferroni_no_correction_at_zero():
    """All p=0 should return [0, 0, ...]."""
    adj = holm_bonferroni([0.0, 0.0, 0.0])
    assert all(a == 0.0 for a in adj)


def test_holm_bonferroni_rejects_fewer_than_plain():
    """With 22 tests at α=0.05, Holm should reject fewer than naive α=0.05."""
    import random as rnd
    rnd.seed(42)
    ps = [rnd.uniform(0, 0.10) for _ in range(22)]
    naive_rejects = sum(1 for p in ps if p < 0.05)
    adj = holm_bonferroni(ps)
    holm_rejects = sum(1 for a in adj if a < 0.05)
    assert holm_rejects <= naive_rejects


def test_holm_bonferroni_preserves_length():
    ps = [0.01, 0.04, 0.03, 0.20, 0.001]
    adj = holm_bonferroni(ps)
    assert len(adj) == len(ps)


def test_tercile_delta_positive():
    x = list(range(30))
    y = [v * 2.0 for v in x]   # perfect linear
    delta = tercile_delta(x, y)
    assert delta > 0


def test_mean_delta_by_group_sign():
    x = [1] * 10 + [0] * 10
    y = [60.0] * 10 + [45.0] * 10
    delta = mean_delta_by_group(x, y)
    assert delta > 0


# ---------------------------------------------------------------------------
# Synthetic positive: Seth fixture should produce qualifying mood→HRV finding
# ---------------------------------------------------------------------------

def test_synthetic_positive_seth():
    df = _load_fixture("seth_28d.json")
    findings = _run_all_hypotheses(df)

    mood_hrv = next(
        (f for f in findings
         if f["predictor"] == "subjective.mood" and f["response"] == "recovery_signal.hrv_ms"),
        None
    )
    assert mood_hrv is not None
    assert mood_hrv["n"] >= MIN_N
    assert abs(mood_hrv["r"]) >= MIN_ABS_R
    assert mood_hrv["p"] < MAX_P
    assert mood_hrv["qualifies"] is True
    # Effect should reference a positive ms delta (good mood → higher HRV)
    assert mood_hrv["effect"] > 0


def test_synthetic_positive_effect_unit():
    df = _load_fixture("seth_28d.json")
    findings = _run_all_hypotheses(df)
    mood_hrv = next(f for f in findings
                    if f["predictor"] == "subjective.mood" and f["response"] == "recovery_signal.hrv_ms")
    assert mood_hrv["effect_unit"] == "ms"


# ---------------------------------------------------------------------------
# Synthetic null: random data should produce very few qualifying rows across
# 50 seeds. Holm-Bonferroni allows ~5% of runs to still produce one FP
# (P(≥1 FP per run) ≈ 5% at α=0.05). Without correction it would be ~68%
# per run. We assert ≤ 3 runs (out of 50) have any qualifying finding.
# ---------------------------------------------------------------------------

def test_synthetic_null_holm_correction():
    """With Holm correction, ≤ 3 out of 50 random seeds should qualify."""
    fp_seeds = []
    for seed in range(50):
        df = _random_df(seed)
        findings = _run_all_hypotheses(df)
        if any(f["qualifies"] for f in findings):
            fp_seeds.append(seed)
    assert len(fp_seeds) <= 3, (
        f"Too many false positives: seeds {fp_seeds}. "
        "Holm-Bonferroni correction may not be working."
    )


# ---------------------------------------------------------------------------
# Insufficient data: 10 days should produce no qualifying findings
# ---------------------------------------------------------------------------

def test_insufficient_data_no_qualifies():
    df = _random_df(1, n=10)
    findings = _run_all_hypotheses(df)
    assert all(not f["qualifies"] for f in findings)


def test_insufficient_data_n_below_min():
    df = _random_df(1, n=10)
    findings = _run_all_hypotheses(df)
    # All n values should be below MIN_N (lag-1 pairs can be at most n-1 = 9)
    for f in findings:
        if f["predictor"] != "sleep_bedtime_hour":  # always NaN
            assert f["n"] < MIN_N or not f["qualifies"]


# ---------------------------------------------------------------------------
# Lag wiring: same-day correlation near zero, lagged correlation is strong
# ---------------------------------------------------------------------------

def test_lag_wiring():
    """
    Build a fixture where mood(d) predicts hrv(d+1) strongly but
    mood(d) has no relationship to hrv(d).
    """
    n = 28
    rng = np.random.default_rng(123)
    start = date(2025, 2, 1)
    idx = [start + timedelta(days=i) for i in range(n)]
    moods = np.array([2.0, 4.0, 7.0, 9.0] * 7)
    # hrv(d) = 40 + moods(d-1)*3 + noise  → strong lag=1, weak same-day
    hrv = np.array([40 + moods[i - 1] * 3.5 + rng.uniform(-1.5, 1.5) if i > 0
                    else 50.0 for i in range(n)])

    cols = {c: np.full(n, np.nan) for c in [
        "subjective.energy", "subjective.motivation", "subjective.clarity",
        "recovery_signal.rhr_bpm", "recovery_signal.sleep_h",
        "recovery_signal.sleep_eff_pct", "recovery_signal.strain",
        "nutrition.protein_g", "nutrition.kcal_total", "nutrition.fiber_g",
        "nutrition.caffeine_late_mg", "nutrition.alcohol_units",
        "vice_log_bool", "wins.walk_bool", "wins.meditate_bool", "wins.lift_bool",
        "workout_log.completed_count", "workout_log.strain_avg_7d",
        "weight_lb_delta_7d", "sleep_bedtime_hour",
    ]}
    cols["subjective.mood"] = moods.astype(float)
    cols["recovery_signal.hrv_ms"] = hrv

    df = pd.DataFrame(cols, index=idx)

    # The lagged hypothesis
    hyp_lagged = next(h for h in HYPOTHESES
                      if h.predictor == "subjective.mood"
                      and h.response == "recovery_signal.hrv_ms"
                      and h.lag_days == 1)
    f = _compute_hypothesis(df, hyp_lagged)
    assert abs(f["r"]) >= MIN_ABS_R, f"lagged r={f['r']:.3f} expected >= {MIN_ABS_R}"


# ---------------------------------------------------------------------------
# Effect sign: alcohol → lower HRV should produce effect < 0
# ---------------------------------------------------------------------------

def test_effect_sign_negative():
    """Higher alcohol units → lower HRV next morning: effect should be negative."""
    n = 28
    rng = np.random.default_rng(55)
    start = date(2025, 3, 1)
    idx = [start + timedelta(days=i) for i in range(n)]
    # Alternate high/low alcohol days
    alcohol = np.array([3.0 if i % 2 == 0 else 0.0 for i in range(n)])
    # HRV drops on days after high alcohol
    hrv = np.array([45.0 + rng.uniform(-2, 2) if alcohol[i - 1] > 1 else 62.0 + rng.uniform(-2, 2)
                    if i > 0 else 55.0 for i in range(n)])

    df = pd.DataFrame({
        "nutrition.alcohol_units": alcohol,
        "recovery_signal.hrv_ms": hrv,
        **{c: np.full(n, np.nan) for c in [
            "subjective.mood", "subjective.energy", "subjective.motivation", "subjective.clarity",
            "recovery_signal.rhr_bpm", "recovery_signal.sleep_h",
            "recovery_signal.sleep_eff_pct", "recovery_signal.strain",
            "nutrition.protein_g", "nutrition.kcal_total", "nutrition.fiber_g",
            "nutrition.caffeine_late_mg",
            "vice_log_bool", "wins.walk_bool", "wins.meditate_bool", "wins.lift_bool",
            "workout_log.completed_count", "workout_log.strain_avg_7d",
            "weight_lb_delta_7d", "sleep_bedtime_hour",
        ]},
    }, index=idx)

    hyp = next(h for h in HYPOTHESES
               if h.predictor == "nutrition.alcohol_units"
               and h.response == "recovery_signal.hrv_ms"
               and h.lag_days == 1)
    f = _compute_hypothesis(df, hyp)
    assert f["r"] < 0, f"expected negative r, got r={f['r']:.3f}"
    assert f["effect"] < 0, f"expected negative effect, got effect={f['effect']:.1f}"


# ---------------------------------------------------------------------------
# Idempotency: computing twice for the same fixture gives the same r
# ---------------------------------------------------------------------------

def test_idempotency_same_r():
    df = _load_fixture("seth_28d.json")
    hyp = next(h for h in HYPOTHESES
               if h.predictor == "subjective.mood" and h.response == "recovery_signal.hrv_ms")
    f1 = _compute_hypothesis(df, hyp)
    f2 = _compute_hypothesis(df, hyp)
    assert f1["r"] == f2["r"]
    assert f1["n"] == f2["n"]


# ---------------------------------------------------------------------------
# Per-user isolation: Seth and Slav fixtures produce different r values
# ---------------------------------------------------------------------------

def test_per_user_isolation():
    df_seth = _load_fixture("seth_28d.json")
    df_slav = _load_fixture("slav_28d.json")
    hyp = next(h for h in HYPOTHESES
               if h.predictor == "subjective.mood" and h.response == "recovery_signal.hrv_ms")
    f_seth = _compute_hypothesis(df_seth, hyp)
    f_slav = _compute_hypothesis(df_slav, hyp)
    # Both are strong correlations in the same direction, but the effect sizes differ.
    assert f_seth["r"] != f_slav["r"]
    assert f_seth["n"] == f_slav["n"]  # same fixture length


# ---------------------------------------------------------------------------
# Forbidden phrases: no generic health advice in the hypothesis registry labels
# ---------------------------------------------------------------------------

FORBIDDEN_PHRASES = ["stay hydrated", "your recovery is high", "great job", "keep it up"]

def test_hypothesis_labels_no_forbidden_phrases():
    for hyp in HYPOTHESES:
        for phrase in FORBIDDEN_PHRASES:
            assert phrase.lower() not in hyp.human_predictor.lower()
            assert phrase.lower() not in hyp.human_response.lower()
