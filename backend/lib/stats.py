"""
T-30 — statistical helpers for the pattern engine.

New backend dependency: scipy (called out per CLAUDE.md "no new dependencies
without flagging" rule — approved alongside pandas in the T-30 PR).

Spearman is the default for all continuous/ordinal pairs because mood is an
ordinal scale and HRV has heavy tails. Pearson is intentionally not exposed
for v1; see T-30 Gotchas.

Holm-Bonferroni is implemented inline (8 lines) rather than pulling statsmodels.
"""

import numpy as np
from scipy import stats as scipy_stats


def spearman(x: list, y: list) -> tuple[float, float, int]:
    """Spearman rank correlation. Returns (r, p_two_tailed, n_complete)."""
    ax = np.array(x, dtype=float)
    ay = np.array(y, dtype=float)
    mask = np.isfinite(ax) & np.isfinite(ay)
    ax, ay = ax[mask], ay[mask]
    n = int(len(ax))
    if n < 3:
        return 0.0, 1.0, n
    r, p = scipy_stats.spearmanr(ax, ay)
    return float(r), float(p), n


def point_biserial(x_bool: list, y: list) -> tuple[float, float, int]:
    """Point-biserial correlation for a boolean predictor. Returns (r, p, n)."""
    ax = np.array(x_bool, dtype=float)
    ay = np.array(y, dtype=float)
    mask = np.isfinite(ax) & np.isfinite(ay)
    ax, ay = ax[mask], ay[mask]
    n = int(len(ax))
    if n < 3:
        return 0.0, 1.0, n
    r, p = scipy_stats.pointbiserialr(ax, ay)
    return float(r), float(p), n


def holm_bonferroni(p_values: list[float]) -> list[float]:
    """
    Holm-Bonferroni step-down correction. Returns adjusted p-values in the
    same order as the input. Keeps all input indices so the caller can zip
    results back to their hypothesis list without re-sorting.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running_max = 0.0
    for rank, idx in enumerate(order):
        adj = (m - rank) * p_values[idx]
        running_max = max(running_max, adj)
        adjusted[idx] = min(running_max, 1.0)
    return adjusted


def mean_delta_by_group(x_bool: list, y: list) -> float:
    """
    Effect size for boolean predictor: mean(y | x=True) − mean(y | x=False).
    Returns 0.0 if either group is empty after pairwise-complete filtering.
    """
    ax = np.array(x_bool, dtype=float)
    ay = np.array(y, dtype=float)
    mask = np.isfinite(ax) & np.isfinite(ay)
    ax, ay = ax[mask], ay[mask]
    y_true  = ay[ax > 0.5]
    y_false = ay[ax <= 0.5]
    if len(y_true) == 0 or len(y_false) == 0:
        return 0.0
    return float(np.mean(y_true) - np.mean(y_false))


def tercile_delta(x: list, y: list) -> float:
    """
    Effect size for ordinal/continuous predictor:
    mean(y | x >= p67) − mean(y | x <= p33).
    Terciles are within-window so the range is per-user only — never cross-user.
    Returns 0.0 if either tercile group is empty.
    """
    ax = np.array(x, dtype=float)
    ay = np.array(y, dtype=float)
    mask = np.isfinite(ax) & np.isfinite(ay)
    ax, ay = ax[mask], ay[mask]
    if len(ax) < 6:
        return 0.0
    p33 = float(np.percentile(ax, 33.33))
    p67 = float(np.percentile(ax, 66.67))
    y_high = ay[ax >= p67]
    y_low  = ay[ax <= p33]
    if len(y_high) == 0 or len(y_low) == 0:
        return 0.0
    return float(np.mean(y_high) - np.mean(y_low))
