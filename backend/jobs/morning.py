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
    from jobs.patterns import run_patterns

    await db.init()
    failures = 0
    for user in USERS:
        uid = user["id"]
        today = date.today()

        # Step 1: compose brief (reads yesterday's patterns via _build_snapshot).
        log.info("Composing brief for user=%s", uid)
        brief_result = None
        try:
            brief_result = await compose_brief(uid, today)
            state = brief_result.get("state", "ok")
            log.info("Brief for user=%s — state=%s", uid, state)
        except Exception as exc:
            log.error("Brief failed for user=%s: %s", uid, exc)
            failures += 1

        # Step 2: daily brief push (T-36). Non-fatal.
        if brief_result:
            try:
                from jobs.notify import send_brief_push
                cc = brief_result.get("coaching_card") or {}
                headline = cc.get("headline") or brief_result.get("headline") or ""
                if headline:
                    await send_brief_push(uid, headline)
            except Exception as exc:
                log.warning("Brief push failed for user=%s: %s", uid, exc)

        # Step 3: run pattern engine for tonight's patterns (available to
        # tomorrow's brief). Runs after brief so it never blocks the brief.
        log.info("Running pattern engine for user=%s", uid)
        try:
            findings = await run_patterns(uid, today)
            n_q = sum(1 for f in findings if f["qualifies"])
            log.info("Patterns for user=%s — qualifying=%d", uid, n_q)
        except Exception as exc:
            log.error("Patterns failed for user=%s: %s", uid, exc)
            # Non-fatal — the brief is already written.

    await db.close()

    if failures:
        log.error("%d/%d users failed brief.", failures, len(USERS))
        sys.exit(1)

    log.info("Morning job complete.")


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
