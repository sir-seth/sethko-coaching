"""
Sethko Coaching — one-time DB seed script

Run this once after your first Railway deploy to:
  1. Insert Seth and Slav as users
  2. Migrate Whoop tokens from your local tokens.json into the DB

Usage:
    DATABASE_URL=<your-railway-postgres-url> python seed_users.py
    DATABASE_URL=<your-railway-postgres-url> python seed_users.py --tokens-file ~/code/sethko-coaching/tokens.json

Get DATABASE_URL from the Railway dashboard:
  Your project → Postgres service → Connect tab → copy the "Postgres Connection URL"
"""

import argparse
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg

# Map Whoop user IDs (from tokens.json) to our app user IDs.
WHOOP_TO_APP_USER = {
    "37338043": "seth",
}

USERS = [
    {
        "id": "seth",
        "name": "Seth",
        "email": None,
        "dietary_modality": "maintenance_active",
        "goal": "cut",
        "mode": "gentle",
        "recovery_source": "whoop",
    },
]


async def seed(tokens_file):
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("Set DATABASE_URL before running this script.")

    conn = await asyncpg.connect(dsn)

    print("Creating tables if needed...")
    from db import _CREATE_TABLES_SQL
    await conn.execute(_CREATE_TABLES_SQL)

    for u in USERS:
        await conn.execute(
            """
            INSERT INTO users (id, name, email, dietary_modality, goal, mode, recovery_source)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (id) DO UPDATE SET
                dietary_modality = EXCLUDED.dietary_modality,
                goal             = EXCLUDED.goal,
                mode             = EXCLUDED.mode,
                recovery_source  = EXCLUDED.recovery_source
            """,
            u["id"], u["name"], u["email"],
            u["dietary_modality"], u["goal"], u["mode"], u["recovery_source"],
        )
        print(f"  Upserted user: {u['id']} (mode={u['mode']})")

    if tokens_file and tokens_file.exists():
        tokens = json.loads(tokens_file.read_text())
        print(f"\nMigrating tokens from {tokens_file} ({len(tokens)} entries)...")

        for whoop_user_id, token_data in tokens.items():
            app_user_id = WHOOP_TO_APP_USER.get(whoop_user_id)
            if not app_user_id:
                print(f"  SKIP: no mapping for Whoop user {whoop_user_id}. Add to WHOOP_TO_APP_USER.")
                continue

            obtained_at = token_data.get("obtained_at")
            expires_in = token_data.get("expires_in", 3600)
            if obtained_at:
                obtained_dt = datetime.fromisoformat(obtained_at.replace("Z", "+00:00"))
                expires_at = obtained_dt + timedelta(seconds=expires_in)
            else:
                expires_at = None

            await conn.execute(
                """
                INSERT INTO whoop_tokens
                    (user_id, access_token, refresh_token, expires_at, whoop_user_id)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (user_id) DO UPDATE SET
                    access_token  = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    expires_at    = EXCLUDED.expires_at,
                    whoop_user_id = EXCLUDED.whoop_user_id,
                    updated_at    = NOW()
                """,
                app_user_id,
                token_data["access_token"],
                token_data.get("refresh_token"),
                expires_at,
                whoop_user_id,
            )
            print(f"  Migrated tokens for {app_user_id} (Whoop user {whoop_user_id})")
    else:
        if tokens_file:
            print(f"\nTokens file not found at {tokens_file} — skipping token migration.")
        else:
            print("\nNo --tokens-file given — skipping token migration.")

    await conn.close()
    print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tokens-file", type=Path, help="Path to tokens.json from whoop_pull.py")
    args = parser.parse_args()
    asyncio.run(seed(args.tokens_file))
