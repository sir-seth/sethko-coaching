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
    CoachingResponse,
    HealthSnapshot,
    RecoverySignal,
    User,
    WeightPoint,
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
    recovery_source   TEXT NOT NULL DEFAULT 'whoop',
    macro_targets     JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, started_at)
);

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
    return User(
        id=row["id"],
        name=row["name"],
        email=row["email"],
        dietary_modality=row["dietary_modality"],
        goal=row["goal"],
        recovery_source=row["recovery_source"],
        macro_targets=row["macro_targets"],
        created_at=row["created_at"],
    )


async def upsert_user(user: User):
    async with _conn() as conn:
        await conn.execute(
            """
            INSERT INTO users (id, name, email, dietary_modality, goal, recovery_source, macro_targets)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                email = EXCLUDED.email,
                dietary_modality = EXCLUDED.dietary_modality,
                goal = EXCLUDED.goal,
                recovery_source = EXCLUDED.recovery_source,
                macro_targets = EXCLUDED.macro_targets
            """,
            user.id,
            user.name,
            user.email,
            user.dietary_modality,
            user.goal,
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
                    (user_id, started_at, ended_at, sport_name, duration_min,
                     avg_hr_bpm, calories, source, raw)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (user_id, started_at) DO UPDATE SET
                    ended_at     = EXCLUDED.ended_at,
                    duration_min = EXCLUDED.duration_min,
                    avg_hr_bpm   = EXCLUDED.avg_hr_bpm,
                    calories     = EXCLUDED.calories
                """,
                user_id,
                w.started_at,
                w.ended_at,
                w.activity_label,
                w.duration_min,
                w.avg_hr_bpm,
                w.calories,
                "healthkit",
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
    data = row["response"]
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
# Seed helpers (called from seed_users.py, not from the app)
# ---------------------------------------------------------------------------

async def seed_default_users():
    """
    Insert Seth and Slav if they don't exist yet.
    Run once after first deploy: python seed_users.py
    """
    seth = User(
        id="seth",
        name="Seth",
        email=None,
        dietary_modality="maintenance_active",
        goal="cut",
        recovery_source="whoop",
    )
    slav = User(
        id="slav",
        name="Slav",
        email=None,
        dietary_modality="high_protein_performance",
        goal="bulk",
        recovery_source="whoop",
    )
    for u in [seth, slav]:
        await upsert_user(u)
        log.info("Seeded user: %s", u.id)
