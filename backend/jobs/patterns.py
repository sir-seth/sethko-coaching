"""
T-30 — nightly pattern-engine job.

Entry point: run_patterns(user_id, as_of) -> list[dict]

Called by morning.py after compose_brief; the brief therefore reads yesterday's
patterns, which is the correct lifecycle (see T-30 Gotchas).

Also runnable as a CLI for back-fill / one-off runs:
  python -m backend.jobs.patterns --user-id seth --as-of 2025-06-01
"""

import argparse
import asyncio
import logging
from datetime import date

import numpy as np
import pandas as pd

from lib.hypotheses import HYPOTHESES, MIN_N, MIN_ABS_R, MAX_P, Hypothesis
from lib.stats import spearman, point_biserial, holm_bonferroni, mean_delta_by_group, tercile_delta

log = logging.getLogger(__name__)

WINDOW_DAYS = 28


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_patterns(user_id: str, as_of: date) -> list[dict]:
    """
    Run all 22 hypotheses for user_id over a 28-day window ending as_of.
    Persists one row per hypothesis (qualifies=False rows are still written
    so the absence of a finding is auditable). Returns the full list.
    """
    import db
    from lib.dataframe_load import load_dataframe
    from lib.patterns_llm import generate_copy_for_findings

    user = await db.get_user(user_id)
    user_mode = user.mode if user else "gentle"

    df = await load_dataframe(user_id, as_of, window_days=WINDOW_DAYS)

    # Phase 1: compute raw statistics for all 22 hypotheses.
    raw_findings = [_compute_hypothesis(df, h) for h in HYPOTHESES]

    # Phase 2: Holm-Bonferroni correction across all 22 p-values.
    raw_ps = [f["p_raw"] for f in raw_findings]
    adj_ps = holm_bonferroni(raw_ps)

    for i, f in enumerate(raw_findings):
        f["p"] = adj_ps[i]
        f["qualifies"] = (
            f["n"] >= MIN_N
            and abs(f["r"]) >= MIN_ABS_R
            and f["p"] < MAX_P
        )

    # Phase 3: generate LLM copy for qualifying findings (cap = 4 calls/run).
    enriched = generate_copy_for_findings(raw_findings, user_mode)

    # Phase 4: persist all rows.
    for f in enriched:
        row = {
            "predictor":       f["predictor"],
            "response":        f["response"],
            "lag_days":        f["lag_days"],
            "window_days":     WINDOW_DAYS,
            "n":               f["n"],
            "r":               f["r"],
            "p":               f["p"],
            "effect":          f["effect"],
            "effect_unit":     f["effect_unit"],
            "qualifies":       f["qualifies"],
            "headline_payload": f.get("headline_payload", {"before": "", "italic": "", "after": ""}),
            "body":            f.get("body", ""),
        }
        await db.upsert_pattern(user_id, row)

    n_qualifying = sum(1 for f in enriched if f["qualifies"])
    log.info("patterns: user=%s as_of=%s qualifying=%d/%d", user_id, as_of, n_qualifying, len(enriched))
    return enriched


# ---------------------------------------------------------------------------
# Recent patterns summary for coaching brief injection
# ---------------------------------------------------------------------------

async def get_recent_patterns_summary(user_id: str, top_n: int = 3) -> list[str]:
    """
    Return up to top_n qualifying findings as short strings for the coaching
    prompt, e.g. ["subjective.mood ↔ recovery_signal.hrv_ms (r=0.67, n=21)"].
    Returns [] if none qualify.
    """
    import db
    rows = await db.get_patterns_drivers(user_id, "recovery_signal.hrv_ms", limit=top_n)
    # Also grab top overall if not already covered.
    top = await db.get_patterns_top(user_id)
    seen = {r["predictor"] + r["response"] for r in rows}
    if top and (top["predictor"] + top["response"]) not in seen:
        rows = [top] + rows[:top_n - 1]

    return [
        f"{r['predictor']} ↔ {r['response']} (r={r['r']:.2f}, n={r['n']})"
        for r in rows[:top_n]
    ]


# ---------------------------------------------------------------------------
# Internal: compute one hypothesis against the DataFrame
# ---------------------------------------------------------------------------

def _compute_hypothesis(df: pd.DataFrame, h: Hypothesis) -> dict:
    """
    Extract (x, y) vectors with the appropriate lag, run the correlation,
    compute the effect size, and return a raw finding dict (p not yet adjusted).
    """
    x_col = h.predictor
    y_col = h.response

    if x_col not in df.columns or y_col not in df.columns:
        return _null_finding(h)

    if h.lag_days == 0:
        x_vals = df[x_col].values
        y_vals = df[y_col].values
    else:
        # predictor on day d paired with response on day d + lag_days
        x_vals = df[x_col].values[: -h.lag_days]
        y_vals = df[y_col].values[h.lag_days :]

    # Pairwise-complete: both values must be finite.
    mask = np.isfinite(x_vals.astype(float)) & np.isfinite(y_vals.astype(float))
    x_clean = x_vals[mask].tolist()
    y_clean = y_vals[mask].tolist()

    if len(x_clean) < 3:
        return _null_finding(h)

    if h.kind == "boolean":
        r, p_raw, n = point_biserial(x_clean, y_clean)
        effect = mean_delta_by_group(x_clean, y_clean)
    else:
        r, p_raw, n = spearman(x_clean, y_clean)
        effect = tercile_delta(x_clean, y_clean)

    return {
        "predictor":       h.predictor,
        "response":        h.response,
        "lag_days":        h.lag_days,
        "kind":            h.kind,
        "human_predictor": h.human_predictor,
        "human_response":  h.human_response,
        "effect_unit":     h.effect_unit,
        "n":               n,
        "r":               float(r),
        "p_raw":           float(p_raw),
        "p":               float(p_raw),   # will be overwritten after Holm correction
        "effect":          float(effect),
        "qualifies":       False,           # will be set after correction
    }


def _null_finding(h: Hypothesis) -> dict:
    return {
        "predictor":       h.predictor,
        "response":        h.response,
        "lag_days":        h.lag_days,
        "kind":            h.kind,
        "human_predictor": h.human_predictor,
        "human_response":  h.human_response,
        "effect_unit":     h.effect_unit,
        "n":               0,
        "r":               0.0,
        "p_raw":           1.0,
        "p":               1.0,
        "effect":          0.0,
        "qualifies":       False,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def _cli_main(user_id: str, as_of: date):
    import db
    await db.init()
    findings = await run_patterns(user_id, as_of)
    qualifying = [f for f in findings if f["qualifies"]]
    if qualifying:
        for f in qualifying:
            print(f"  ✓ {f['predictor']} → {f['response']} | r={f['r']:.3f} p={f['p']:.4f} n={f['n']} effect={f['effect']:.1f}{f['effect_unit']}")
    else:
        print("  No qualifying findings.")
    await db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", default="seth")
    parser.add_argument("--as-of", default=str(date.today()))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(_cli_main(args.user_id, date.fromisoformat(args.as_of)))


if __name__ == "__main__":
    main()
