"""
Push notification jobs (T-36).

Two entry points — set up as separate Railway crons:

  Daily brief push (after brief is written by morning.py):
    python -m backend.jobs.notify brief

  Check-in reminder (~90 min after wake window):
    python -m backend.jobs.notify checkin

Schedule example (UTC, assuming 7 AM ET wake window):
  Morning cron:  0 12 * * *   (runs morning.py + calls notify brief)
  Reminder cron: 30 13 * * *  (runs notify checkin — 90 min later)

The check-in job queries the database at fire time, so last-minute check-ins
suppress the push (the ticket requires database-at-fire-time, not schedule-time).
"""

import asyncio
import logging
import sys
from datetime import date

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def send_brief_push(user_id: str, headline: str) -> None:
    """
    Send the Daily Brief push for a single user.
    Called from morning.py immediately after compose_brief writes brief_today.
    headline: the coaching_card.headline string from the brief payload.
    """
    import db
    from lib.apns import send_push

    users = await db.get_users_for_push("brief")
    target = next((u for u in users if u["user_id"] == user_id), None)
    if not target:
        log.info("No brief push needed for user=%s (not opted-in or no token)", user_id)
        return

    for tok in target["tokens"]:
        ok = await send_push(
            device_token=tok["token"],
            env=tok["env"],
            title="Today’s brief",
            body=headline,
            category="DAILY_BRIEF",
            collapse_id=f"brief-{user_id}-{date.today().isoformat()}",
        )
        if ok:
            break  # one successful delivery is enough


async def _run_brief() -> None:
    """Standalone: send brief pushes for all opted-in users with a brief today."""
    import db
    from lib.apns import send_push

    await db.init()
    today = date.today()
    users = await db.get_users_for_push("brief")
    log.info("Brief push: %d opted-in user(s)", len(users))

    for u in users:
        uid = u["user_id"]
        brief = await db.get_brief_today(uid, today)
        if not brief:
            log.info("No brief found for user=%s, skipping push", uid)
            continue
        cc = brief.get("coaching_card") or {}
        headline = cc.get("headline") or brief.get("headline") or ""
        if not headline:
            log.info("No headline in brief for user=%s, skipping push", uid)
            continue
        for tok in u["tokens"]:
            ok = await send_push(
                device_token=tok["token"],
                env=tok["env"],
                title="Today’s brief",
                body=headline,
                category="DAILY_BRIEF",
                collapse_id=f"brief-{uid}-{today.isoformat()}",
            )
            if ok:
                break

    await db.close()


async def _run_checkin() -> None:
    """
    Standalone: send check-in reminders for opted-in users who haven't
    checked in yet today. Run this cron ~90 min after the brief push.
    Fires at most once per day per user (the cron itself is the rate-limit gate).
    """
    import db
    from lib.apns import send_push

    await db.init()
    today = date.today()
    users = await db.get_users_for_push("checkin")
    log.info("Check-in reminder: %d opted-in user(s)", len(users))

    # Fixed copy — never LLM-generated, never variable length.
    BODY = "Four questions, about 15 seconds. You don’t have to be specific."

    for u in users:
        uid = u["user_id"]
        log_entry = await db.get_subjective_log(uid, today)
        if log_entry is not None:
            log.info("user=%s already checked in — skipping reminder", uid)
            continue
        for tok in u["tokens"]:
            ok = await send_push(
                device_token=tok["token"],
                env=tok["env"],
                title="A quick check-in?",
                body=BODY,
                category="CHECKIN_REMIND",
                collapse_id=f"checkin-{uid}-{today.isoformat()}",
            )
            if ok:
                break

    await db.close()


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("brief", "checkin"):
        print("Usage: python -m backend.jobs.notify [brief|checkin]", file=sys.stderr)
        sys.exit(1)
    if sys.argv[1] == "brief":
        asyncio.run(_run_brief())
    else:
        asyncio.run(_run_checkin())


if __name__ == "__main__":
    main()
