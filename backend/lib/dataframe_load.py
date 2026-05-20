"""
T-30 — daily-keyed DataFrame builder for the pattern engine.

Assembles one row per calendar day for the requested window, joining all
relevant tables. Column names match the hypothesis registry keys exactly so
the engine can look them up without translation.

Columns present in v1:
  subjective.mood, subjective.energy, subjective.motivation, subjective.clarity
  recovery_signal.hrv_ms, recovery_signal.rhr_bpm, recovery_signal.sleep_h,
  recovery_signal.sleep_eff_pct, recovery_signal.strain
  nutrition.protein_g, nutrition.kcal_total
  nutrition.fiber_g        — always NaN until nutrition_log gains that column
  nutrition.caffeine_late_mg — always NaN until nutrition_log gains that column
  nutrition.alcohol_units  — always NaN until nutrition_log gains that column
  vice_log_bool
  wins.walk_bool, wins.meditate_bool, wins.lift_bool
  workout_log.completed_count, workout_log.strain_avg_7d
  weight_lb_delta_7d
  sleep_bedtime_hour       — extracted from recovery_signal.raw if present

All missing values are NaN. No forward-filling — pairwise-complete is enforced
at hypothesis-compute time.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd


async def load_dataframe(
    user_id: str,
    as_of: date,
    window_days: int = 28,
) -> pd.DataFrame:
    """
    Return a DataFrame indexed by date (Python date objects), covering
    [as_of - window_days + 1 … as_of]. All rows exist even for days with no
    data (values are NaN).
    """
    import db  # local import — avoids circular dep when used as a lib

    start = as_of - timedelta(days=window_days - 1)

    # Build the full date spine so missing days are represented as NaN rows.
    all_dates = [start + timedelta(days=i) for i in range(window_days)]
    df = pd.DataFrame(index=all_dates)
    df.index.name = "date"

    async with db._conn() as conn:
        # --- subjective_log ---
        rows = await conn.fetch(
            """
            SELECT date, mood_numeric, energy, motivation, clarity
            FROM subjective_log
            WHERE user_id = $1 AND date >= $2 AND date <= $3
            """,
            user_id, start, as_of,
        )
        if rows:
            subj = pd.DataFrame(
                [(r["date"], r["mood_numeric"], r["energy"], r["motivation"], r["clarity"]) for r in rows],
                columns=["date", "subjective.mood", "subjective.energy",
                         "subjective.motivation", "subjective.clarity"],
            ).set_index("date")
            df = df.join(subj)

        # --- recovery_signal (one row per date, prefer most recent source) ---
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (date)
                date, hrv_ms, rhr_bpm, sleep_hours, sleep_score, strain_score, raw
            FROM recovery_signal
            WHERE user_id = $1 AND date >= $2 AND date <= $3
            ORDER BY date, created_at DESC
            """,
            user_id, start, as_of,
        )
        if rows:
            rec_data = []
            for r in rows:
                bedtime = _extract_bedtime(r["raw"])
                rec_data.append((
                    r["date"],
                    r["hrv_ms"],
                    r["rhr_bpm"],
                    r["sleep_hours"],
                    r["sleep_score"],
                    r["strain_score"],
                    bedtime,
                ))
            rec = pd.DataFrame(
                rec_data,
                columns=["date", "recovery_signal.hrv_ms", "recovery_signal.rhr_bpm",
                         "recovery_signal.sleep_h", "recovery_signal.sleep_eff_pct",
                         "recovery_signal.strain", "sleep_bedtime_hour"],
            ).set_index("date")
            df = df.join(rec)

        # --- nutrition_log (daily aggregates) ---
        rows = await conn.fetch(
            """
            SELECT
                DATE(logged_at AT TIME ZONE 'UTC') AS date,
                COALESCE(SUM(kcal), 0)       AS kcal_total,
                COALESCE(SUM(protein_g), 0)  AS protein_g
            FROM nutrition_log
            WHERE user_id = $1
              AND logged_at >= $2
              AND logged_at < $3
            GROUP BY DATE(logged_at AT TIME ZONE 'UTC')
            """,
            user_id,
            start,
            as_of + timedelta(days=1),
        )
        if rows:
            nut = pd.DataFrame(
                [(r["date"], r["kcal_total"], r["protein_g"]) for r in rows],
                columns=["date", "nutrition.kcal_total", "nutrition.protein_g"],
            ).set_index("date")
            df = df.join(nut)

        # Columns not yet stored in the DB — always NaN in v1.
        for col in ("nutrition.fiber_g", "nutrition.caffeine_late_mg", "nutrition.alcohol_units"):
            if col not in df.columns:
                df[col] = np.nan

        # --- vice_log (boolean per day) ---
        rows = await conn.fetch(
            """
            SELECT DISTINCT DATE(logged_at AT TIME ZONE 'UTC') AS date
            FROM vice_log
            WHERE user_id = $1
              AND logged_at >= $2
              AND logged_at < $3
            """,
            user_id,
            start,
            as_of + timedelta(days=1),
        )
        if rows:
            vice_dates = set(r["date"] for r in rows)
            df["vice_log_bool"] = df.index.map(lambda d: 1.0 if d in vice_dates else np.nan)
            # Days with no vice entry should be 0 (not NaN) on days with any
            # other data — but if the user has zero data at all that day, keep NaN
            # so pairwise-complete filtering excludes them.
            has_data = df.drop(columns=["vice_log_bool"], errors="ignore").notna().any(axis=1)
            df.loc[has_data & df["vice_log_bool"].isna(), "vice_log_bool"] = 0.0
        else:
            df["vice_log_bool"] = np.nan

        # --- win_log (category booleans per day) ---
        rows = await conn.fetch(
            """
            SELECT date, category FROM win_log
            WHERE user_id = $1 AND date >= $2 AND date <= $3
            """,
            user_id, start, as_of,
        )
        win_data: dict[date, dict[str, bool]] = {}
        for r in rows:
            d = r["date"]
            if d not in win_data:
                win_data[d] = {}
            win_data[d][r["category"]] = True

        for col, cat in [
            ("wins.walk_bool",     "walk"),
            ("wins.meditate_bool", "meditate"),
            ("wins.lift_bool",     "lift"),
        ]:
            df[col] = df.index.map(
                lambda d, c=cat: 1.0 if win_data.get(d, {}).get(c) else (
                    0.0 if win_data.get(d) is not None or d in win_data else np.nan
                )
            )
        # Fill 0 on days with any data (absence of win ≠ missing data)
        has_data = df[["recovery_signal.hrv_ms", "subjective.mood"]].notna().any(axis=1)
        for col in ("wins.walk_bool", "wins.meditate_bool", "wins.lift_bool"):
            df.loc[has_data & df[col].isna(), col] = 0.0

        # --- workout_log (confirmed count per day) ---
        rows = await conn.fetch(
            """
            SELECT
                DATE(started_at AT TIME ZONE 'UTC') AS date,
                COUNT(*)::REAL AS completed_count,
                MAX(strain_score) AS strain_max
            FROM workout_log
            WHERE user_id = $1
              AND confirmed = TRUE
              AND started_at >= $2
              AND started_at < $3
            GROUP BY DATE(started_at AT TIME ZONE 'UTC')
            """,
            user_id,
            start,
            as_of + timedelta(days=1),
        )
        if rows:
            wl = pd.DataFrame(
                [(r["date"], r["completed_count"], r["strain_max"]) for r in rows],
                columns=["date", "workout_log.completed_count", "_strain_per_day"],
            ).set_index("date")
            df = df.join(wl)
        # Fill 0 completed_count on days with any data.
        if "workout_log.completed_count" not in df.columns:
            df["workout_log.completed_count"] = np.nan
        df.loc[has_data & df["workout_log.completed_count"].isna(), "workout_log.completed_count"] = 0.0

        # Trailing 7-day mean of confirmed workout strain.
        if "_strain_per_day" not in df.columns:
            df["_strain_per_day"] = np.nan
        df["workout_log.strain_avg_7d"] = (
            df["_strain_per_day"].rolling(7, min_periods=1).mean()
        )
        df.drop(columns=["_strain_per_day"], inplace=True)

        # --- health_metrics (weight) ---
        rows = await conn.fetch(
            """
            SELECT date, weight_lbs FROM health_metrics
            WHERE user_id = $1 AND date >= $2 AND date <= $3
            ORDER BY date
            """,
            user_id, start, as_of,
        )
        if rows:
            hm = pd.DataFrame(
                [(r["date"], r["weight_lbs"]) for r in rows],
                columns=["date", "_weight_lbs"],
            ).set_index("date")
            df = df.join(hm)
        else:
            df["_weight_lbs"] = np.nan

        # 7-day weight delta.
        df["weight_lb_delta_7d"] = df["_weight_lbs"].diff(7)
        df.drop(columns=["_weight_lbs"], inplace=True)

    # Ensure all expected columns exist (NaN if nothing loaded).
    for col in [
        "subjective.mood", "subjective.energy", "subjective.motivation", "subjective.clarity",
        "recovery_signal.hrv_ms", "recovery_signal.rhr_bpm", "recovery_signal.sleep_h",
        "recovery_signal.sleep_eff_pct", "recovery_signal.strain",
        "nutrition.protein_g", "nutrition.kcal_total", "nutrition.fiber_g",
        "nutrition.caffeine_late_mg", "nutrition.alcohol_units",
        "vice_log_bool", "wins.walk_bool", "wins.meditate_bool", "wins.lift_bool",
        "workout_log.completed_count", "workout_log.strain_avg_7d",
        "weight_lb_delta_7d", "sleep_bedtime_hour",
    ]:
        if col not in df.columns:
            df[col] = np.nan

    # Cast everything to float so NaN arithmetic is consistent.
    df = df.astype(float)
    return df


def _extract_bedtime(raw) -> float | None:
    """
    Try to pull a decimal local bedtime hour from the device raw JSONB blob.
    Whoop puts it under 'bedtime_start'; Oura under 'sleep.bedtime_start'.
    Returns e.g. 22.5 for 10:30pm. Returns None if not present.
    """
    if not raw:
        return None
    try:
        raw_dict = raw if isinstance(raw, dict) else {}
        # Whoop shape
        bt = raw_dict.get("bedtime_start") or raw_dict.get("sleep", {}).get("bedtime_start")
        if bt is None:
            return None
        # bt is typically an ISO datetime string; take the hour + minute fraction.
        from datetime import datetime
        if isinstance(bt, str):
            dt = datetime.fromisoformat(bt.replace("Z", "+00:00"))
        elif isinstance(bt, datetime):
            dt = bt
        else:
            return None
        return dt.hour + dt.minute / 60.0
    except Exception:
        return None
