"""
Morning coaching job — called by Railway cron.

Set up in Railway dashboard:
  Service type: Cron Job
  Schedule: 0 12 * * *  (noon UTC = 7am ET; adjust for DST as needed)
  Command: python -m backend.jobs.morning

Env vars required (shared from main service):
  DATABASE_URL     — Railway Postgres connection string
  ANTHROPIC_API_KEY
  WHOOP_CLIENT_ID / WHOOP_CLIENT_SECRET
  API_KEY          — same key the iOS app uses
"""

import asyncio
import logging
import sys
from datetime import date

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# User IDs. Hardcoded for Phase 1 per T-09.
USERS = [
    {"id": "seth"},
    {"id": "slav"},
]


async def _run():
    import db
    from jobs.coaching import compose_brief

    await db.init()
    failures = 0
    for user in USERS:
        uid = user["id"]
        log.info("Composing brief for user=%s", uid)
        try:
            result = await compose_brief(uid, date.today())
            state = result.get("state", "ok")
            log.info("Brief for user=%s — state=%s", uid, state)
        except Exception as exc:
            log.error("Brief failed for user=%s: %s", uid, exc)
            failures += 1

    await db.close()

    if failures:
        log.error("%d/%d users failed.", failures, len(USERS))
        sys.exit(1)

    log.info("Morning job complete.")


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
