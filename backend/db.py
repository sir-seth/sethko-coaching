"""
Sethko Coaching — database layer (asyncpg + Postgres)

All SQL lives here. main.py and coach.py call these functions and never
touch SQL directly. That keeps the query surface auditable in one file.

Connection string comes from the DATABASE_URL env var, which Railway
injects automatically when you provision a Postgres database.

Table creation is idempotent — safe to call on every startup.
"""

import json
import logging
import os
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import asyncpg

from models import (
    CheckInRequest,
    CoachingResponse,
    DayMacros,
    FoodEntry,
    FoodEntryRequest,
    HealthSnapshot,
    LogEntry,
    MOOD_NUMERIC,
    PendingWorkout,
    RecoverySignal,
    SubjectiveLog,
    User,
    ViceCategory,
    ViceCategoryRequest,
    ViceLogEntry,
    ViceLogResponse,
    WeightPoint,
    WeekDayStatus,
    WinEntry,
    WinRequest,
    WorkoutEntry,
)

log = logging.getLogger(__name__)

_pool: None


# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------

async def init():
    global _pool
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL env var is not set.")
    _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    await _create_tables()


async def close():
    if _pool:
        await _pool.close()


def _conn():
    """Return a connection from the pool (use as async context manager)."""
    return _pool.acquire()


# ---------------------------------------------------------------------------
# Schema (idempotent)
# ---------------------------------------------------------------------------

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    email             TEXT,
    dietary_modality  TEXT NOT NULL DEFAULT 'flexible',
    goal              TEXT NOT NULL DEFAULT 'maintain',
    mode              TEXT DEFAULT NULL,   -- NULL = not yet chosen; T-24 first-run flow
    recovery_source   TEXT NOT NULL DEFAULT 'whoop',
    device            TEXT DEFAULT NULL,   -- active wearable: "whoop" | "oura" | null (T-25)
    oura_raw          JSONB,               -- Oura tokens + last-pulled cursor (T-25)
    sync_state        JSONB,               -- { synced_at, error } per device (T-25)
    macro_targets     JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add mode column if upgrading from a schema that predates it (T-24).
ALTER TABLE users ADD COLUMN IF NOT EXISTS mode TEXT DEFAULT NULL;
-- Drop NOT NULL constraint on mode for existing installs that had DEFAULT 'gentle'.
ALTER TABLE users ALTER COLUMN mode DROP NOT NULL;
-- T-25: Oura device columns.
ALTER TABLE users ADD COLUMN IF NOT EXISTS device TEXT DEFAULT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS oura_raw JSONB;
ALTER TABLE users ADD COLUMN IF NOT EXISTS sync_state JSONB;
-- T-41: Onboarding columns.
ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_completed_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS habits JSONB;

CREATE TABLE IF NOT EXISTS whoop_tokens (
    user_id        TEXT PRIMARY KEY REFERENCES users(id),
    access_token   TEXT NOT NULL,
    refresh_token  TEXT,
    expires_at     TIMESTAMPTZ,
    whoop_user_id  TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Normalised recovery data, same shape regardless of source (Whoop or Oura).
CREATE TABLE IF NOT EXISTS recovery_signal (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id),
    date            DATE NOT NULL,
    source          TEXT NOT NULL,
    hrv_ms          REAL,
    rhr_bpm         REAL,
    sleep_hours     REAL,
    sleep_score     REAL,
    strain_score    REAL,
    recovery_score  REAL,
    raw             JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, date, source)
);

-- Body weight and HealthKit workouts.
CREATE TABLE IF NOT EXISTS health_metrics (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    date        DATE NOT NULL,
    weight_lbs  REAL,
    hrv_ms      REAL,
    steps       INTEGER,
    raw         JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, date)
);

-- One row per workout session (surface-level HealthKit data).
CREATE TABLE IF NOT EXISTS workout_log (
    id             SERIAL PRIMARY KEY,
    user_id        TEXT NOT NULL REFERENCES users(id),
    started_at     TIMESTAMPTZ NOT NULL,
    ended_at       TIMESTAMPTZ NOT NULL,
    modality       TEXT,
    source         TEXT,
    sport_name     TEXT,
    duration_min   REAL,
    avg_hr_bpm     REAL,
    calories       REAL,
    strain_score   REAL,
    notes          TEXT,
    raw            JSONB,
    confirmed      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, started_at)
);
-- Migrate existing installs: add confirmed column if missing.
ALTER TABLE workout_log ADD COLUMN IF NOT EXISTS confirmed BOOLEAN NOT NULL DEFAULT FALSE;

-- Claude's output, one row per user per day.
CREATE TABLE IF NOT EXISTS coaching_outputs (
    id               SERIAL PRIMARY KEY,
    user_id          TEXT NOT NULL REFERENCES users(id),
    date             DATE NOT NULL,
    prompt_snapshot  JSONB,
    response         JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, date)
);

-- Subjective check-in (mood + sliders + note). T-16/T-17.
CREATE TABLE IF NOT EXISTS subjective_log (
    id           SERIAL PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id),
    date         DATE NOT NULL,
    mood_label   TEXT,          -- "rough"|"flat"|"good"|"great"
    mood_numeric SMALLINT,      -- rough=2, flat=4, good=7, great=9 (pattern engine, T-30)
    energy       SMALLINT,      -- 1-10
    motivation   SMALLINT,      -- 1-10
    clarity      SMALLINT,      -- 1-10
    note         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, date)
);
-- Migrate existing installs from the Phase 1 schema (mood INTEGER 1-5).
ALTER TABLE subjective_log ADD COLUMN IF NOT EXISTS mood_label TEXT;
ALTER TABLE subjective_log ADD COLUMN IF NOT EXISTS mood_numeric SMALLINT;
ALTER TABLE subjective_log ADD COLUMN IF NOT EXISTS motivation SMALLINT;
ALTER TABLE subjective_log ADD COLUMN IF NOT EXISTS clarity SMALLINT;

-- Food log entries (T-21).
CREATE TABLE IF NOT EXISTS nutrition_log (
    id           SERIAL PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id),
    logged_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description  TEXT NOT NULL,
    weight_g     REAL,
    kcal         INTEGER,
    protein_g    REAL,
    carbs_g      REAL,
    fat_g        REAL,
    meal_label   TEXT,          -- 'breakfast'|'lunch'|'snack'|'dinner'
    source       TEXT           -- 'manual', 'voice', 'barcode'
);
ALTER TABLE nutrition_log ADD COLUMN IF NOT EXISTS weight_g REAL;
ALTER TABLE nutrition_log ADD COLUMN IF NOT EXISTS meal_label TEXT;
CREATE INDEX IF NOT EXISTS nutrition_log_user_time ON nutrition_log (user_id, logged_at);

-- USDA food cache to avoid repeated API calls (T-21).
CREATE TABLE IF NOT EXISTS recent_foods (
    id              SERIAL PRIMARY KEY,
    query           TEXT NOT NULL UNIQUE,
    fdc_id          INTEGER,
    description     TEXT NOT NULL,
    kcal_per_100g   REAL NOT NULL DEFAULT 0,
    protein_per_100g REAL NOT NULL DEFAULT 0,
    fat_per_100g    REAL NOT NULL DEFAULT 0,
    carbs_per_100g  REAL NOT NULL DEFAULT 0,
    cached_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Vice category list — user-defined, private, never sent to Claude (T-22).
CREATE TABLE IF NOT EXISTS vice_categories (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    label       TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    deleted_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS vice_categories_user ON vice_categories (user_id) WHERE deleted_at IS NULL;

-- Vice log — timestamp only. category_id is the only optional detail. No freetext ever.
CREATE TABLE IF NOT EXISTS vice_log (
    id           SERIAL PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id),
    logged_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    category_id  INTEGER REFERENCES vice_categories(id)
    -- NO notes, description, or freetext columns. The privacy promise is the product.
);
CREATE INDEX IF NOT EXISTS vice_log_user_time ON vice_log (user_id, logged_at);

-- Win log (T-18). One row per win; multiple wins allowed per day.
CREATE TABLE IF NOT EXISTS win_log (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    date        DATE NOT NULL,
    text        TEXT NOT NULL,
    meta        TEXT,
    category    TEXT NOT NULL DEFAULT 'other',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS win_log_user_date ON win_log (user_id, date);

-- Daily coaching brief (T-23). Replaces coaching_outputs as the iOS-facing payload.
CREATE TABLE IF NOT EXISTS brief_today (
    user_id      TEXT NOT NULL REFERENCES users(id),
    date         DATE NOT NULL,
    payload      JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, date)
);

-- Planned activities — user or Sethko-committed plans for a day (T-27).
CREATE TABLE IF NOT EXISTS planned_activities (
    id          SERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    date        DATE NOT NULL,
    category    TEXT NOT NULL,
    name        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'user',   -- 'sethko'|'user'
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS planned_activities_user_date ON planned_activities (user_id, date);

-- T-26: Sign in with Apple
ALTER TABLE users ADD COLUMN IF NOT EXISTS apple_sub TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS users_apple_sub ON users (apple_sub) WHERE apple_sub IS NOT NULL;

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions (user_id);

-- Backfill sentinel apple_sub for seed users so dev X-Seed-User bypass works.
UPDATE users SET apple_sub = 'seed:' || id
WHERE id IN ('seth', 'slav', 'oura_test') AND apple_sub IS NULL;

-- T-29: manual workout logger
ALTER TABLE workout_log ADD COLUMN IF NOT EXISTS session_source TEXT DEFAULT 'device';
CREATE TABLE IF NOT EXISTS workout_sets (
    id            SERIAL PRIMARY KEY,
    workout_id    INTEGER NOT NULL REFERENCES workout_log(id) ON DELETE CASCADE,
    exercise_name TEXT NOT NULL,
    set_idx       INTEGER NOT NULL,
    reps          INTEGER,
    weight_lb     REAL,
    rpe           SMALLINT,
    skipped       BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS workout_sets_workout ON workout_sets (workout_id);

-- T-36: push notification registration
ALTER TABLE users ADD COLUMN IF NOT EXISTS wake_window   TIME    NOT NULL DEFAULT '07:00';
ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_brief  BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_checkin BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS device_tokens (
    token         TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    env           TEXT NOT NULL DEFAULT 'sandbox',   -- 'sandbox' | 'production'
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS device_tokens_user ON device_tokens (user_id);

-- T-30: pattern engine findings
CREATE TABLE IF NOT EXISTS patterns (
    id              BIGSERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    predictor       TEXT NOT NULL,
    response        TEXT NOT NULL,
    lag_days        SMALLINT NOT NULL,
    window_days     SMALLINT NOT NULL,
    n               SMALLINT NOT NULL,
    r               REAL NOT NULL,
    p               REAL NOT NULL,
    effect          REAL NOT NULL,
    effect_unit     TEXT NOT NULL,
    qualifies       BOOLEAN NOT NULL,
    headline_payload JSONB NOT NULL DEFAULT '{}',
    body            TEXT NOT NULL DEFAULT '',
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, predictor, response, lag_days, window_days)
);
CREATE INDEX IF NOT EXISTS patterns_user_qualifies
    ON patterns (user_id, qualifies, computed_at DESC);
"""


async def _create_tables():
    async with _conn() as conn:
        await conn.execute(_CREATE_TABLES_SQL)
    log.info("Tables verified / created.")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def get_user(user_id: str) -> Optional[User]:
    async with _conn() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    if not row:
        return None
    habits_raw = row["habits"]
    habits = json.loads(habits_raw) if isinstance(habits_raw, str) else habits_raw
    return User(
        id=row["id"],
        name=row["name"],
        email=row["email"],
        dietary_modality=row["dietary_modality"],
        goal=row["goal"],
        mode=row["mode"],
        recovery_source=row["recovery_source"],
        device=row["device"],
        macro_targets=row["macro_targets"],
        onboarding_completed_at=row["onboarding_completed_at"],
        habits=habits,
        created_at=row["created_at"],
    )


async def upsert_user(user: User):
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT INTO users (id, name, email, dietary_modality, goal, mode, recovery_source, macro_targets)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (id) DO UPDATE SET
                name             = EXCLUDED.name,
                email            = EXCLUDED.email,
                dietary_modality = EXCLUDED.dietary_modality,
                goal             = EXCLUDED.goal,
                mode             = EXCLUDED.mode,
                recovery_source  = EXCLUDED.recovery_source,
                macro_targets    = EXCLUDED.macro_targets
            """,
            user.id,
            user.name,
            user.email,
            user.dietary_modality,
            user.goal,
            user.mode,
            user.recovery_source,
            json.dumps(user.macro_targets) if user.macro_targets else None,
        )


# ---------------------------------------------------------------------------
# Whoop tokens
# ---------------------------------------------------------------------------

async def get_whoop_tokens(user_id: str) -> Optional[dict]:
    async with _conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM whoop_tokens WHERE user_id = $1", user_id
        )
    if not row:
        return None
    return dict(row)


async def save_whoop_tokens(user_id: str, tokens: dict):
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT INTO whoop_tokens (user_id, access_token, refresh_token, expires_at, whoop_user_id)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id) DO UPDATE SET
                access_token  = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                expires_at    = EXCLUDED.expires_at,
                whoop_user_id = EXCLUDED.whoop_user_id,
                updated_at    = NOW()
            """,
            user_id,
            tokens["access_token"],
            tokens.get("refresh_token"),
            tokens.get("expires_at"),
            tokens.get("whoop_user_id"),
        )


# ---------------------------------------------------------------------------
# Recovery signal
# ---------------------------------------------------------------------------

async def upsert_recovery_signal(user_id: str, signal: RecoverySignal):
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT INTO recovery_signal
                (user_id, date, source, hrv_ms, rhr_bpm, sleep_hours, sleep_score,
                 strain_score, recovery_score)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (user_id, date, source) DO UPDATE SET
                hrv_ms         = EXCLUDED.hrv_ms,
                rhr_bpm        = EXCLUDED.rhr_bpm,
                sleep_hours    = EXCLUDED.sleep_hours,
                sleep_score    = EXCLUDED.sleep_score,
                strain_score   = EXCLUDED.strain_score,
                recovery_score = EXCLUDED.recovery_score
            """,
            user_id,
            signal.date,
            signal.source,
            signal.hrv_ms,
            signal.rhr_bpm,
            signal.sleep_hours,
            signal.sleep_score,
            signal.strain_score,
            signal.recovery_score,
        )


async def get_latest_recovery_signal(user_id: str) -> Optional[RecoverySignal]:
    async with _conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM recovery_signal
            WHERE user_id = $1
            ORDER BY date DESC
            LIMIT 1
            """,
            user_id,
        )
    if not row:
        return None
    return RecoverySignal(**{k: row[k] for k in RecoverySignal.model_fields if k in row})


async def get_recovery_signals(user_id: str, days: int = 7) -> list[RecoverySignal]:
    cutoff = date.today() - timedelta(days=days)
    async with _conn() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM recovery_signal
            WHERE user_id = $1 AND date >= $2
            ORDER BY date DESC
            """,
            user_id,
            cutoff,
        )
    return [RecoverySignal(**{k: r[k] for k in RecoverySignal.model_fields if k in r}) for r in rows]


# ---------------------------------------------------------------------------
# Health metrics (HealthKit weight + HRV)
# ---------------------------------------------------------------------------

async def upsert_health_metrics(user_id: str, snapshot: HealthSnapshot):
    async with _conn() as conn:
        # Upsert the daily health row.
        await conn.execute(
            """
            INSERT INTO health_metrics (user_id, date, weight_lbs, hrv_ms, steps, raw)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (user_id, date) DO UPDATE SET
                weight_lbs = COALESCE(EXCLUDED.weight_lbs, health_metrics.weight_lbs),
                hrv_ms     = COALESCE(EXCLUDED.hrv_ms, health_metrics.hrv_ms),
                steps      = COALESCE(EXCLUDED.steps, health_metrics.steps)
            """,
            user_id,
            snapshot.date,
            snapshot.weight_lbs,
            snapshot.hrv_ms,
            snapshot.steps,
            json.dumps({"source": "healthkit"}),
        )

        # Upsert each workout.
        for w in snapshot.workouts:
            await conn.execute(
                """
                INSERT INTO workout_log
                    (user_id, started_at, ended_at, modality, sport_name, duration_min,
                     avg_hr_bpm, calories, source, raw)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (user_id, started_at) DO UPDATE SET
                    ended_at     = EXCLUDED.ended_at,
                    modality     = EXCLUDED.modality,
                    duration_min = EXCLUDED.duration_min,
                    avg_hr_bpm   = EXCLUDED.avg_hr_bpm,
                    calories     = EXCLUDED.calories
                """,
                user_id,
                w.started_at,
                w.ended_at,
                w.modality or w.activity_type,
                w.activity_label,
                w.duration_min,
                w.avg_hr_bpm,
                w.calories,
                w.source_app or "healthkit",
                json.dumps({"activity_type": w.activity_type, "source_app": w.source_app}),
            )


async def get_latest_health_metrics(user_id: str) -> Optional[dict]:
    async with _conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM health_metrics
            WHERE user_id = $1
            ORDER BY date DESC
            LIMIT 1
            """,
            user_id,
        )
    return dict(row) if row else None


async def get_weight_trend(user_id: str, days: int = 30) -> list[WeightPoint]:
    cutoff = date.today() - timedelta(days=days)
    async with _conn() as conn:
        rows = await conn.fetch(
            """
            SELECT date, weight_lbs FROM health_metrics
            WHERE user_id = $1 AND date >= $2 AND weight_lbs IS NOT NULL
            ORDER BY date ASC
            """,
            user_id,
            cutoff,
        )
    return [WeightPoint(date=r["date"], weight_lbs=r["weight_lbs"]) for r in rows]


# ---------------------------------------------------------------------------
# Workouts
# ---------------------------------------------------------------------------

async def get_recent_workouts(user_id: str, days: int = 7) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with _conn() as conn:
        rows = await conn.fetch(
            """
            SELECT started_at, ended_at, sport_name, duration_min, avg_hr_bpm,
                   calories, strain_score, source
            FROM workout_log
            WHERE user_id = $1 AND started_at >= $2
            ORDER BY started_at DESC
            """,
            user_id,
            cutoff,
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Coaching outputs
# ---------------------------------------------------------------------------

async def get_coaching_output(user_id: str, for_date: date) -> Optional[CoachingResponse]:
    async with _conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT response, created_at FROM coaching_outputs
            WHERE user_id = $1 AND date = $2
            """,
            user_id,
            for_date,
        )
    if not row:
        return None
    data = json.loads(row["response"]) if isinstance(row["response"], str) else row["response"]
    data["generated_at"] = row["created_at"]
    return CoachingResponse(**data)


async def save_coaching_output(user_id: str, digest: dict, response: dict):
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT INTO coaching_outputs (user_id, date, prompt_snapshot, response)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, date) DO UPDATE SET
                prompt_snapshot = EXCLUDED.prompt_snapshot,
                response        = EXCLUDED.response,
                created_at      = NOW()
            """,
            user_id,
            date.today(),
            json.dumps(digest),
            json.dumps(response),
        )


# ---------------------------------------------------------------------------
# Mode update (T-24)
# ---------------------------------------------------------------------------

async def update_user_mode(user_id: str, mode: str):
    async with _conn() as conn:
        await conn.execute(
            "UPDATE users SET mode = $1 WHERE id = $2",
            mode,
            user_id,
        )


async def update_user_profile(
    user_id: str,
    goal: Optional[str] = None,
    habits: Optional[list] = None,
    onboarding_completed_at=None,
):
    """Partial update for onboarding-set fields. Only touches columns that are non-None."""
    sets, vals = [], [user_id]
    if goal is not None:
        vals.append(goal)
        sets.append(f"goal = ${len(vals)}")
    if habits is not None:
        vals.append(json.dumps(habits))
        sets.append(f"habits = ${len(vals)}")
    if onboarding_completed_at is not None:
        vals.append(onboarding_completed_at)
        sets.append(f"onboarding_completed_at = ${len(vals)}")
    if not sets:
        return
    async with _conn() as conn:
        await conn.execute(
            f"UPDATE users SET {', '.join(sets)} WHERE id = $1",
            *vals,
        )


# ---------------------------------------------------------------------------
# Oura tokens + device status (T-25)
# ---------------------------------------------------------------------------

async def get_oura_tokens(user_id: str) -> Optional[dict]:
    async with _conn() as conn:
        row = await conn.fetchrow("SELECT oura_raw FROM users WHERE id = $1", user_id)
    if not row or not row["oura_raw"]:
        return None
    raw = row["oura_raw"]
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not data.get("access_token"):
        return None
    return data


async def save_oura_tokens(user_id: str, tokens: dict):
    """Merge tokens into users.oura_raw, preserving any other fields (e.g. pending_verifier)."""
    async with _conn() as conn:
        row = await conn.fetchrow("SELECT oura_raw FROM users WHERE id = $1", user_id)
        existing = {}
        if row and row["oura_raw"]:
            existing = json.loads(row["oura_raw"]) if isinstance(row["oura_raw"], str) else row["oura_raw"]
        merged = {**existing, **tokens}
        await conn.execute(
            "UPDATE users SET oura_raw = $1::jsonb WHERE id = $2",
            json.dumps(merged),
            user_id,
        )


async def save_oura_pkce_verifier(user_id: str, verifier: str):
    """Store the PKCE code_verifier temporarily until callback completes."""
    async with _conn() as conn:
        row = await conn.fetchrow("SELECT oura_raw FROM users WHERE id = $1", user_id)
        existing = {}
        if row and row["oura_raw"]:
            existing = json.loads(row["oura_raw"]) if isinstance(row["oura_raw"], str) else row["oura_raw"]
        existing["pending_verifier"] = verifier
        await conn.execute(
            "UPDATE users SET oura_raw = $1::jsonb WHERE id = $2",
            json.dumps(existing),
            user_id,
        )


async def get_oura_pkce_verifier(user_id: str) -> Optional[str]:
    """Read the pending PKCE code_verifier stored before the OAuth redirect."""
    async with _conn() as conn:
        row = await conn.fetchrow("SELECT oura_raw FROM users WHERE id = $1", user_id)
    if not row or not row["oura_raw"]:
        return None
    raw = row["oura_raw"]
    data = json.loads(raw) if isinstance(raw, str) else raw
    return data.get("pending_verifier")


async def clear_oura_pkce_verifier(user_id: str):
    async with _conn() as conn:
        row = await conn.fetchrow("SELECT oura_raw FROM users WHERE id = $1", user_id)
        if row and row["oura_raw"]:
            raw = json.loads(row["oura_raw"]) if isinstance(row["oura_raw"], str) else row["oura_raw"]
            raw.pop("pending_verifier", None)
            await conn.execute(
                "UPDATE users SET oura_raw = $1::jsonb WHERE id = $2",
                json.dumps(raw),
                user_id,
            )


async def update_user_device(user_id: str, device: Optional[str]):
    async with _conn() as conn:
        await conn.execute(
            "UPDATE users SET device = $1 WHERE id = $2",
            device,
            user_id,
        )


async def update_sync_state(user_id: str, state: dict):
    async with _conn() as conn:
        await conn.execute(
            "UPDATE users SET sync_state = $1::jsonb WHERE id = $2",
            json.dumps(state),
            user_id,
        )


async def get_device_status(user_id: str) -> dict:
    """
    Returns { device, synced_at, error } used by GET /api/devices and the masthead chip.
    synced_at comes from the latest recovery_signal row for this user.
    """
    async with _conn() as conn:
        user_row = await conn.fetchrow(
            "SELECT device, sync_state FROM users WHERE id = $1", user_id
        )
        if not user_row:
            return {"device": None, "synced_at": None, "error": None}

        sync_state_raw = user_row["sync_state"]
        sync_state = {}
        if sync_state_raw:
            sync_state = json.loads(sync_state_raw) if isinstance(sync_state_raw, str) else sync_state_raw

        # Use the timestamp of the latest recovery_signal row as synced_at.
        latest = await conn.fetchrow(
            """
            SELECT created_at FROM recovery_signal
            WHERE user_id = $1
            ORDER BY date DESC, created_at DESC
            LIMIT 1
            """,
            user_id,
        )
        synced_at = latest["created_at"].isoformat() if latest else None

        return {
            "device":    user_row["device"],
            "synced_at": synced_at,
            "error":     sync_state.get("error"),
        }


async def disconnect_oura(user_id: str):
    async with _conn() as conn:
        await conn.execute(
            "UPDATE users SET device = NULL, oura_raw = NULL WHERE id = $1",
            user_id,
        )


# ---------------------------------------------------------------------------
# Daily brief — brief_today (T-23)
# ---------------------------------------------------------------------------

async def save_brief_today(user_id: str, for_date: date, payload: dict):
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT INTO brief_today (user_id, date, payload, generated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (user_id, date) DO UPDATE SET
                payload      = EXCLUDED.payload,
                generated_at = NOW()
            """,
            user_id,
            for_date,
            json.dumps(payload),
        )


async def get_brief_today(user_id: str, for_date: date) -> Optional[dict]:
    async with _conn() as conn:
        row = await conn.fetchrow(
            "SELECT payload FROM brief_today WHERE user_id = $1 AND date = $2",
            user_id,
            for_date,
        )
    if not row:
        return None
    data = row["payload"]
    return json.loads(data) if isinstance(data, str) else data


# ---------------------------------------------------------------------------
# Plan index (T-27)
# ---------------------------------------------------------------------------

def _docket_meta(created_at) -> str:
    hour = created_at.hour
    h = hour % 12 or 12
    ampm = "AM" if hour < 12 else "PM"
    time_of_day = "this morning" if hour < 12 else ("this afternoon" if hour < 17 else "this evening")
    return f"~{h}:00 {ampm} · planned {time_of_day}"


async def get_docket_items(user_id: str, for_date: date) -> list[dict]:
    async with _conn() as conn:
        rows = await conn.fetch(
            """
            SELECT id, category, name, created_at
            FROM planned_activities
            WHERE user_id = $1 AND date = $2
            ORDER BY created_at ASC
            """,
            user_id,
            for_date,
        )
    return [
        {
            "id": row["id"],
            "category": row["category"],
            "name": row["name"],
            "meta": _docket_meta(row["created_at"]),
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]


async def commit_plan(user_id: str, for_date: date, category: str, name: str) -> dict:
    async with _conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO planned_activities (user_id, date, category, name, source)
            VALUES ($1, $2, $3, $4, 'user')
            RETURNING id, category, name, created_at
            """,
            user_id,
            for_date,
            category,
            name,
        )
    return {
        "id": row["id"],
        "category": row["category"],
        "name": row["name"],
        "meta": _docket_meta(row["created_at"]),
        "created_at": row["created_at"].isoformat(),
    }


# ---------------------------------------------------------------------------
# Subjective check-in (T-17)
# ---------------------------------------------------------------------------

async def upsert_subjective_log(user_id: str, payload: CheckInRequest):
    mood_numeric = MOOD_NUMERIC[payload.mood]
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT INTO subjective_log
                (user_id, date, mood_label, mood_numeric, energy, motivation, clarity, note)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (user_id, date) DO UPDATE SET
                mood_label   = EXCLUDED.mood_label,
                mood_numeric = EXCLUDED.mood_numeric,
                energy       = EXCLUDED.energy,
                motivation   = EXCLUDED.motivation,
                clarity      = EXCLUDED.clarity,
                note         = EXCLUDED.note,
                created_at   = NOW()
            """,
            user_id,
            payload.date,
            payload.mood,
            mood_numeric,
            payload.energy,
            payload.motivation,
            payload.clarity,
            payload.note,
        )


async def get_subjective_log(user_id: str, for_date: date) -> Optional[SubjectiveLog]:
    async with _conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT date, mood_label, mood_numeric, energy, motivation, clarity, note, created_at
            FROM subjective_log
            WHERE user_id = $1 AND date = $2
            """,
            user_id,
            for_date,
        )
    if not row:
        return None
    return SubjectiveLog(
        date=row["date"],
        mood_label=row["mood_label"],
        mood_numeric=row["mood_numeric"],
        energy=row["energy"],
        motivation=row["motivation"],
        clarity=row["clarity"],
        note=row["note"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Log index (T-20)
# ---------------------------------------------------------------------------

async def get_pending_workouts(user_id: str, days: int = 3) -> list[PendingWorkout]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with _conn() as conn:
        rows = await conn.fetch(
            """
            SELECT id, source, sport_name, modality, started_at, duration_min, strain_score
            FROM workout_log
            WHERE user_id = $1 AND confirmed = false AND started_at >= $2
            ORDER BY started_at DESC
            """,
            user_id, cutoff,
        )
    result = []
    for r in rows:
        name = r["sport_name"] or r["modality"] or "Workout"
        dur = f"{int(r['duration_min'])} min" if r["duration_min"] else ""
        detail = f"{name} · {dur}" if dur else name
        result.append(PendingWorkout(
            id=r["id"],
            source=r["source"] or "device",
            detail=detail,
            started_at=r["started_at"],
            duration_min=r["duration_min"],
            strain_score=r["strain_score"],
        ))
    return result


async def confirm_workout(user_id: str, workout_id: int) -> bool:
    async with _conn() as conn:
        result = await conn.execute(
            "UPDATE workout_log SET confirmed = true WHERE id = $1 AND user_id = $2",
            workout_id, user_id,
        )
    return result == "UPDATE 1"


async def get_today_entries(user_id: str, for_date: date) -> list[LogEntry]:
    start = datetime(for_date.year, for_date.month, for_date.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    entries: list[LogEntry] = []

    async with _conn() as conn:
        # Nutrition
        nutrition = await conn.fetch(
            """
            SELECT id::text, description, kcal, protein_g, logged_at
            FROM nutrition_log
            WHERE user_id = $1 AND logged_at >= $2 AND logged_at < $3
            ORDER BY logged_at ASC
            """,
            user_id, start, end,
        )
        for r in nutrition:
            parts = [p for p in [
                f"{r['kcal']} kcal" if r["kcal"] else None,
                f"{int(r['protein_g'])}g pr" if r["protein_g"] else None,
            ] if p]
            entries.append(LogEntry(
                id=f"food-{r['id']}",
                kind="food",
                icon="fork.knife",
                text=r["description"],
                meta=" · ".join(parts) if parts else "",
                logged_at=r["logged_at"],
            ))

        # Confirmed workouts
        workouts = await conn.fetch(
            """
            SELECT id::text, sport_name, modality, duration_min, started_at
            FROM workout_log
            WHERE user_id = $1 AND confirmed = true
              AND started_at >= $2 AND started_at < $3
            ORDER BY started_at ASC
            """,
            user_id, start, end,
        )
        for r in workouts:
            name = r["sport_name"] or r["modality"] or "Workout"
            dur = f"{int(r['duration_min'])} min" if r["duration_min"] else ""
            icon = _workout_icon(r["modality"] or "")
            entries.append(LogEntry(
                id=f"workout-{r['id']}",
                kind="workout",
                icon=icon,
                text=name,
                meta=dur,
                logged_at=r["started_at"],
            ))

        # Check-in
        checkin = await conn.fetchrow(
            """
            SELECT id::text, mood_label, created_at
            FROM subjective_log
            WHERE user_id = $1 AND date = $2
            """,
            user_id, for_date,
        )
        if checkin:
            entries.append(LogEntry(
                id=f"checkin-{checkin['id']}",
                kind="checkin",
                icon="checkmark.seal",
                text="Morning check-in",
                meta=checkin["mood_label"] or "",
                logged_at=checkin["created_at"],
            ))

    entries.sort(key=lambda e: e.logged_at)
    return entries


def _workout_icon(modality: str) -> str:
    m = modality.lower()
    if "strength" in m or "lift" in m: return "dumbbell"
    if "run" in m or "cardio" in m:    return "figure.run"
    if "walk" in m:                    return "figure.walk"
    if "yoga" in m or "mobility" in m: return "figure.yoga"
    if "meditat" in m:                 return "figure.mind.and.body"
    return "figure.mixed.cardio"


# ---------------------------------------------------------------------------
# Win log (T-18)
# ---------------------------------------------------------------------------

async def insert_win(user_id: str, payload: WinRequest) -> WinEntry:
    async with _conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO win_log (user_id, date, text, meta, category)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, date, text, meta, category, created_at
            """,
            user_id,
            payload.date,
            payload.text,
            payload.meta,
            payload.category,
        )
    return WinEntry(
        id=row["id"],
        date=row["date"],
        text=row["text"],
        meta=row["meta"],
        category=row["category"],
        created_at=row["created_at"],
    )


async def get_wins(user_id: str, for_date: date) -> list[WinEntry]:
    async with _conn() as conn:
        rows = await conn.fetch(
            """
            SELECT id, date, text, meta, category, created_at
            FROM win_log
            WHERE user_id = $1 AND date = $2
            ORDER BY created_at ASC
            """,
            user_id,
            for_date,
        )
    return [
        WinEntry(
            id=r["id"],
            date=r["date"],
            text=r["text"],
            meta=r["meta"],
            category=r["category"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


async def get_mood_wins_range(user_id: str, start: date, end: date):
    """Batch fetch subjective logs and win counts for a date range (T-34)."""
    async with _conn() as conn:
        subj_rows = await conn.fetch(
            """
            SELECT date, mood_label, mood_numeric
            FROM subjective_log
            WHERE user_id = $1 AND date >= $2 AND date <= $3
            """,
            user_id, start, end,
        )
        win_rows = await conn.fetch(
            """
            SELECT date, COUNT(*) AS win_count
            FROM win_log
            WHERE user_id = $1 AND date >= $2 AND date <= $3
            GROUP BY date
            """,
            user_id, start, end,
        )
    subj = {r["date"]: r for r in subj_rows}
    wins = {r["date"]: int(r["win_count"]) for r in win_rows}
    return subj, wins


# ---------------------------------------------------------------------------
# Food log (T-21)
# ---------------------------------------------------------------------------

async def insert_food_entry(user_id: str, payload: FoodEntryRequest) -> FoodEntry:
    async with _conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO nutrition_log
                (user_id, description, weight_g, kcal, protein_g, fat_g, carbs_g, meal_label, source)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id, description, weight_g, kcal, protein_g, fat_g, carbs_g, meal_label, logged_at
            """,
            user_id,
            payload.description,
            payload.weight_g,
            payload.kcal,
            payload.protein_g,
            payload.fat_g,
            payload.carbs_g,
            payload.meal_label,
            payload.source,
        )
    return FoodEntry(
        id=row["id"],
        description=row["description"],
        weight_g=row["weight_g"],
        kcal=row["kcal"],
        protein_g=row["protein_g"],
        fat_g=row["fat_g"],
        carbs_g=row["carbs_g"],
        meal_label=row["meal_label"],
        logged_at=row["logged_at"],
    )


async def get_food_entries(user_id: str, for_date: date) -> list[FoodEntry]:
    start = datetime(for_date.year, for_date.month, for_date.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    async with _conn() as conn:
        rows = await conn.fetch(
            """
            SELECT id, description, weight_g, kcal, protein_g, fat_g, carbs_g, meal_label, logged_at
            FROM nutrition_log
            WHERE user_id = $1 AND logged_at >= $2 AND logged_at < $3
            ORDER BY logged_at ASC
            """,
            user_id, start, end,
        )
    return [
        FoodEntry(
            id=r["id"],
            description=r["description"],
            weight_g=r["weight_g"],
            kcal=r["kcal"],
            protein_g=r["protein_g"],
            fat_g=r["fat_g"],
            carbs_g=r["carbs_g"],
            meal_label=r["meal_label"],
            logged_at=r["logged_at"],
        )
        for r in rows
    ]


async def get_day_macros(user_id: str, for_date: date) -> DayMacros:
    start = datetime(for_date.year, for_date.month, for_date.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    async with _conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*)          AS entry_count,
                COALESCE(SUM(kcal), 0)      AS kcal,
                COALESCE(SUM(protein_g), 0) AS protein_g,
                COALESCE(SUM(fat_g), 0)     AS fat_g,
                COALESCE(SUM(carbs_g), 0)   AS carbs_g
            FROM nutrition_log
            WHERE user_id = $1 AND logged_at >= $2 AND logged_at < $3
            """,
            user_id, start, end,
        )
    return DayMacros(
        date=for_date.isoformat(),
        kcal=int(row["kcal"]),
        protein_g=float(row["protein_g"]),
        fat_g=float(row["fat_g"]),
        carbs_g=float(row["carbs_g"]),
        entry_count=int(row["entry_count"]),
    )


async def get_week_food_status(user_id: str, week_dates: list[date]) -> list[WeekDayStatus]:
    today = date.today()
    result = []
    for d in week_dates:
        if d > today:
            result.append(WeekDayStatus(date=d.isoformat(), status="future", kcal=0))
            continue
        macros = await get_day_macros(user_id, d)
        if d == today:
            status = "today"
        elif macros.kcal >= 1000:
            status = "logged"
        elif macros.entry_count > 0:
            status = "partial"
        else:
            status = "skipped"
        result.append(WeekDayStatus(date=d.isoformat(), status=status, kcal=macros.kcal))
    return result


async def cache_food(query: str, usda: dict):
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT INTO recent_foods
                (query, fdc_id, description, kcal_per_100g, protein_per_100g, fat_per_100g, carbs_per_100g)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (query) DO UPDATE SET
                fdc_id           = EXCLUDED.fdc_id,
                description      = EXCLUDED.description,
                kcal_per_100g    = EXCLUDED.kcal_per_100g,
                protein_per_100g = EXCLUDED.protein_per_100g,
                fat_per_100g     = EXCLUDED.fat_per_100g,
                carbs_per_100g   = EXCLUDED.carbs_per_100g,
                cached_at        = NOW()
            """,
            query.lower(),
            usda.get("fdc_id"),
            usda["description"],
            usda["kcal_per_100g"],
            usda["protein_per_100g"],
            usda["fat_per_100g"],
            usda["carbs_per_100g"],
        )


async def lookup_cached_food(query: str) -> Optional[dict]:
    async with _conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT fdc_id, description, kcal_per_100g, protein_per_100g, fat_per_100g, carbs_per_100g
            FROM recent_foods
            WHERE query = $1 AND cached_at > NOW() - INTERVAL '24 hours'
            """,
            query.lower(),
        )
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Vice log (T-22)
# ---------------------------------------------------------------------------

_DEFAULT_CATEGORIES = [
    "alcohol",
    "cannabis",
    "late night eating",
    "doom scrolling",
    "gambling",
]


async def _seed_vice_categories(user_id: str, conn):
    """Insert default categories if the user has none. Called on first vice log."""
    count = await conn.fetchval(
        "SELECT COUNT(*) FROM vice_categories WHERE user_id = $1 AND deleted_at IS NULL",
        user_id,
    )
    if count == 0:
        for i, label in enumerate(_DEFAULT_CATEGORIES):
            await conn.execute(
                "INSERT INTO vice_categories (user_id, label, sort_order) VALUES ($1, $2, $3)",
                user_id, label, i,
            )


async def log_vice(user_id: str, category_id: Optional[int]) -> ViceLogResponse:
    async with _conn() as conn:
        await _seed_vice_categories(user_id, conn)
        row = await conn.fetchrow(
            "INSERT INTO vice_log (user_id, category_id) VALUES ($1, $2) RETURNING id, logged_at",
            user_id, category_id,
        )
    return ViceLogResponse(id=row["id"], logged_at=row["logged_at"])


async def get_vice_categories(user_id: str) -> list[ViceCategory]:
    async with _conn() as conn:
        await _seed_vice_categories(user_id, conn)
        rows = await conn.fetch(
            """
            SELECT id, label, sort_order FROM vice_categories
            WHERE user_id = $1 AND deleted_at IS NULL
            ORDER BY sort_order ASC, created_at ASC
            """,
            user_id,
        )
    return [ViceCategory(id=r["id"], label=r["label"], sort_order=r["sort_order"]) for r in rows]


async def add_vice_category(user_id: str, label: str) -> ViceCategory:
    async with _conn() as conn:
        max_order = await conn.fetchval(
            "SELECT COALESCE(MAX(sort_order), -1) FROM vice_categories WHERE user_id = $1",
            user_id,
        )
        row = await conn.fetchrow(
            "INSERT INTO vice_categories (user_id, label, sort_order) VALUES ($1, $2, $3) RETURNING id, label, sort_order",
            user_id, label[:32], (max_order or 0) + 1,
        )
    return ViceCategory(id=row["id"], label=row["label"], sort_order=row["sort_order"])


async def delete_vice_category(user_id: str, category_id: int) -> bool:
    async with _conn() as conn:
        result = await conn.execute(
            "UPDATE vice_categories SET deleted_at = NOW() WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL",
            category_id, user_id,
        )
    return result == "UPDATE 1"


async def get_recent_vice(user_id: str, days: int = 90) -> list[ViceLogEntry]:
    """Returns timestamp + category_id only — no labels. Used by pattern engine."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with _conn() as conn:
        rows = await conn.fetch(
            """
            SELECT vl.id, vl.logged_at, vl.category_id, vc.label AS category_label
            FROM vice_log vl
            LEFT JOIN vice_categories vc ON vl.category_id = vc.id
            WHERE vl.user_id = $1 AND vl.logged_at >= $2
            ORDER BY vl.logged_at DESC
            """,
            user_id, cutoff,
        )
    return [
        ViceLogEntry(
            id=r["id"],
            logged_at=r["logged_at"],
            category_id=r["category_id"],
            category_label=r["category_label"],
        )
        for r in rows
    ]


async def had_recent_vice(user_id: str) -> bool:
    """True if user logged a vice in the last 24 hours. Used for coaching digest only."""
    async with _conn() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM vice_log WHERE user_id = $1 AND logged_at > NOW() - INTERVAL '24 hours' LIMIT 1",
            user_id,
        )
    return row is not None


async def delete_vice_log_entry(user_id: str, log_id: int) -> bool:
    async with _conn() as conn:
        result = await conn.execute(
            "DELETE FROM vice_log WHERE id = $1 AND user_id = $2",
            log_id, user_id,
        )
    return result == "DELETE 1"


# ---------------------------------------------------------------------------
# Seed helpers (called from seed_users.py, not from the app)
# ---------------------------------------------------------------------------

async def seed_default_users():
    """Insert Seth and Slav if they don't exist. Run once after first deploy."""
    for user in [
        User(id="seth", name="Seth", dietary_modality="maintenance_active", goal="cut",  mode="gentle",    recovery_source="whoop"),
        User(id="slav", name="Slav", dietary_modality="high_protein_performance",  goal="bulk", mode="optimizer", recovery_source="whoop"),
    ]:
        await upsert_user(user)
        log.info("Seeded user: %s", user.id)


# ---------------------------------------------------------------------------
# Sessions (T-26)
# ---------------------------------------------------------------------------

import secrets as _secrets


async def upsert_user_by_apple_sub(
    apple_sub: str,
    email: Optional[str],
    full_name: Optional[str],
) -> dict:
    """
    Create or fetch a user keyed by Apple sub.
    - On first auth: create with defaults + store email if provided.
    - On repeat auth: update email if Apple provided it (shouldn't happen, but safe).
    Returns a plain dict with all user columns.
    """
    name = full_name or (email.split("@")[0] if email else "User")
    async with _conn() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE apple_sub = $1", apple_sub)
        if row:
            if email and not row["email"]:
                await conn.execute(
                    "UPDATE users SET email = $1 WHERE apple_sub = $2", email, apple_sub
                )
                row = await conn.fetchrow("SELECT * FROM users WHERE apple_sub = $1", apple_sub)
            return dict(row)
        user_id = _secrets.token_urlsafe(8)
        row = await conn.fetchrow(
            """
            INSERT INTO users (id, name, email, apple_sub)
            VALUES ($1, $2, $3, $4)
            RETURNING *
            """,
            user_id, name, email, apple_sub,
        )
        log.info("Created new user via SIWA: id=%s apple_sub=%s", user_id, apple_sub[:8] + "…")
        return dict(row)


async def create_session(user_id: str) -> str:
    """Create a new 365-day session. Returns the opaque token."""
    token = _secrets.token_hex(32)  # 64-char hex
    expires_at = datetime.now(timezone.utc) + timedelta(days=365)
    async with _conn() as conn:
        await conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES ($1, $2, $3)",
            token, user_id, expires_at,
        )
    return token


async def resolve_session(token: str) -> Optional[str]:
    """Return user_id for a valid, non-expired session token, or None."""
    async with _conn() as conn:
        row = await conn.fetchrow(
            "SELECT user_id FROM sessions WHERE token = $1 AND expires_at > NOW()",
            token,
        )
    return row["user_id"] if row else None


# ---------------------------------------------------------------------------
# Manual workout logger (T-29)
# ---------------------------------------------------------------------------

async def insert_manual_workout(user_id: str, payload) -> dict:
    """
    Insert a logger-recorded workout into workout_log + workout_sets.
    Returns {workout_id, set_count, exercise_count}.
    """
    from models import WorkoutLogRequest
    duration_min = (payload.ended_at - payload.started_at).total_seconds() / 60

    async with _conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO workout_log
                (user_id, started_at, ended_at, modality, source, session_source,
                 sport_name, duration_min, notes)
            VALUES ($1, $2, $3, 'strength', 'logger', 'logger', 'Lift', $4, $5)
            RETURNING id
            """,
            user_id,
            payload.started_at,
            payload.ended_at,
            duration_min,
            payload.notes,
        )
        workout_id = row["id"]

        set_count = 0
        for ex in payload.exercises:
            if ex.skipped:
                await conn.execute(
                    """
                    INSERT INTO workout_sets
                        (workout_id, exercise_name, set_idx, reps, weight_lb, rpe, skipped)
                    VALUES ($1, $2, 0, NULL, NULL, NULL, TRUE)
                    """,
                    workout_id, ex.name,
                )
            else:
                for i, s in enumerate(ex.sets, start=1):
                    await conn.execute(
                        """
                        INSERT INTO workout_sets
                            (workout_id, exercise_name, set_idx, reps, weight_lb, rpe)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        """,
                        workout_id, ex.name, i, s.reps, s.weight_lb, s.rpe,
                    )
                    set_count += 1

    exercise_count = sum(1 for ex in payload.exercises if not ex.skipped)
    return {"workout_id": workout_id, "set_count": set_count, "exercise_count": exercise_count}


# ---------------------------------------------------------------------------
# Patterns (T-30)
# ---------------------------------------------------------------------------

async def upsert_pattern(user_id: str, finding: dict):
    """Upsert one pattern row. headline_payload is stored as JSONB."""
    import json as _json
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT INTO patterns
                (user_id, predictor, response, lag_days, window_days,
                 n, r, p, effect, effect_unit, qualifies, headline_payload, body, computed_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, NOW())
            ON CONFLICT (user_id, predictor, response, lag_days, window_days) DO UPDATE SET
                n               = EXCLUDED.n,
                r               = EXCLUDED.r,
                p               = EXCLUDED.p,
                effect          = EXCLUDED.effect,
                effect_unit     = EXCLUDED.effect_unit,
                qualifies       = EXCLUDED.qualifies,
                headline_payload = EXCLUDED.headline_payload,
                body            = EXCLUDED.body,
                computed_at     = NOW()
            """,
            user_id,
            finding["predictor"],
            finding["response"],
            finding["lag_days"],
            finding["window_days"],
            finding["n"],
            finding["r"],
            finding["p"],
            finding["effect"],
            finding["effect_unit"],
            finding["qualifies"],
            _json.dumps(finding.get("headline_payload", {})),
            finding.get("body", ""),
        )


async def get_patterns_top(user_id: str) -> Optional[dict]:
    """Return the best qualifying finding (highest abs(r))."""
    async with _conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM patterns
            WHERE user_id = $1 AND qualifies = TRUE
            ORDER BY
                abs(r) DESC,
                abs(effect) DESC,
                CASE WHEN predictor LIKE 'subjective.%%' THEN 0 ELSE 1 END ASC
            LIMIT 1
            """,
            user_id,
        )
    if not row:
        return None
    return _pattern_row_to_dict(row)


async def get_patterns_drivers(user_id: str, response_key: str, limit: int = 5) -> list[dict]:
    """Return qualifying findings for a given response key, sorted by abs(effect) desc."""
    async with _conn() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM patterns
            WHERE user_id = $1 AND response = $2 AND qualifies = TRUE
            ORDER BY abs(effect) DESC
            LIMIT $3
            """,
            user_id, response_key, limit,
        )
    return [_pattern_row_to_dict(r) for r in rows]


async def get_patterns_days_observed(user_id: str, window_days: int = 28) -> int:
    """
    Count distinct calendar days with any data (recovery_signal OR subjective_log)
    within the most recent window_days.
    """
    from datetime import date, timedelta
    start = date.today() - timedelta(days=window_days - 1)
    async with _conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(DISTINCT d)::INTEGER AS n
            FROM (
                SELECT date AS d FROM recovery_signal
                WHERE user_id = $1 AND date >= $2
                UNION
                SELECT date AS d FROM subjective_log
                WHERE user_id = $1 AND date >= $2
            ) t
            """,
            user_id, start,
        )
    return int(row["n"]) if row else 0


def _pattern_row_to_dict(row) -> dict:
    import json as _json
    hp = row["headline_payload"]
    if isinstance(hp, str):
        hp = _json.loads(hp)
    return {
        "id":              row["id"],
        "predictor":       row["predictor"],
        "response":        row["response"],
        "lag_days":        row["lag_days"],
        "window_days":     row["window_days"],
        "n":               row["n"],
        "r":               row["r"],
        "p":               row["p"],
        "effect":          row["effect"],
        "effect_unit":     row["effect_unit"],
        "qualifies":       row["qualifies"],
        "headline_payload": hp,
        "body":            row["body"],
        "computed_at":     row["computed_at"].isoformat() if row["computed_at"] else None,
    }


async def delete_user_cascade(user_id: str):
    """
    Hard-delete a user and all their data.
    Required by Apple App Store guideline 5.1.1(v).
    Sessions cascade via ON DELETE CASCADE on the sessions table.
    All other tables are deleted explicitly.
    """
    async with _conn() as conn:
        for table in (
            "device_tokens",
            "subjective_log",
            "win_log",
            "nutrition_log",
            "vice_log",
            "vice_categories",
            "workout_log",
            "health_metrics",
            "recovery_signal",
            "coaching_outputs",
            "brief_today",
            "whoop_tokens",
        ):
            await conn.execute(f"DELETE FROM {table} WHERE user_id = $1", user_id)
        await conn.execute("DELETE FROM users WHERE id = $1", user_id)
    log.info("Cascade-deleted user and all data: user_id=%s", user_id)


# ---------------------------------------------------------------------------
# Push notifications (T-36)
# ---------------------------------------------------------------------------

async def register_device_token(user_id: str, token: str, env: str) -> None:
    """Upsert a device token. Called on every app launch (idempotent)."""
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT INTO device_tokens (token, user_id, env, registered_at, last_seen)
            VALUES ($1, $2, $3, NOW(), NOW())
            ON CONFLICT (token) DO UPDATE SET
                user_id   = EXCLUDED.user_id,
                env       = EXCLUDED.env,
                last_seen = NOW()
            """,
            token, user_id, env,
        )


async def unregister_device_tokens(user_id: str) -> None:
    """Remove all device tokens for a user (opt-out)."""
    async with _conn() as conn:
        await conn.execute("DELETE FROM device_tokens WHERE user_id = $1", user_id)


async def get_push_prefs(user_id: str) -> dict:
    """Return {notify_brief, notify_checkin, wake_window} for a user."""
    async with _conn() as conn:
        row = await conn.fetchrow(
            "SELECT notify_brief, notify_checkin, wake_window FROM users WHERE id = $1",
            user_id,
        )
    if not row:
        return {"notify_brief": False, "notify_checkin": False, "wake_window": "07:00"}
    wake = row["wake_window"]
    wake_str = wake.strftime("%H:%M") if hasattr(wake, "strftime") else str(wake)[:5]
    return {
        "notify_brief":   row["notify_brief"],
        "notify_checkin": row["notify_checkin"],
        "wake_window":    wake_str,
    }


async def update_push_prefs(
    user_id: str,
    notify_brief: Optional[bool] = None,
    notify_checkin: Optional[bool] = None,
    wake_window: Optional[str] = None,
) -> None:
    """Partial-update push preferences."""
    updates = []
    params: list = []
    idx = 1
    if notify_brief is not None:
        updates.append(f"notify_brief = ${idx}");  params.append(notify_brief);  idx += 1
    if notify_checkin is not None:
        updates.append(f"notify_checkin = ${idx}"); params.append(notify_checkin); idx += 1
    if wake_window is not None:
        updates.append(f"wake_window = ${idx}");   params.append(wake_window);   idx += 1
    if not updates:
        return
    params.append(user_id)
    sql = f"UPDATE users SET {', '.join(updates)} WHERE id = ${idx}"
    async with _conn() as conn:
        await conn.execute(sql, *params)


async def get_users_for_push(kind: str) -> list[dict]:
    """
    Return users whose push flag is enabled and who have at least one device token.
    kind: 'brief' | 'checkin'
    Each dict: {user_id, wake_window, tokens: [{token, env}]}
    """
    flag_col = "notify_brief" if kind == "brief" else "notify_checkin"
    async with _conn() as conn:
        rows = await conn.fetch(
            f"""
            SELECT u.id AS user_id,
                   u.wake_window,
                   dt.token,
                   dt.env
            FROM users u
            JOIN device_tokens dt ON dt.user_id = u.id
            WHERE u.{flag_col} = TRUE
            ORDER BY u.id, dt.last_seen DESC
            """,
        )
    # Group tokens per user
    by_user: dict[str, dict] = {}
    for r in rows:
        uid = r["user_id"]
        if uid not in by_user:
            wake = r["wake_window"]
            wake_str = wake.strftime("%H:%M") if hasattr(wake, "strftime") else str(wake)[:5]
            by_user[uid] = {"user_id": uid, "wake_window": wake_str, "tokens": []}
        by_user[uid]["tokens"].append({"token": r["token"], "env": r["env"]})
    return list(by_user.values())
