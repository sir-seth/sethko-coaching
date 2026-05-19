"""
Morning coaching job — called by Railway cron.

Set up in Railway dashboard:
  Service type: Cron Job
  Schedule: 0 12 * * *  (noon UTC = 7am ET; adjust for DST as needed)
  Command: cd backend && python jobs/morning.py

Env vars required (shared from main service):
  BACKEND_URL  — internal URL of the FastAPI service (Railway injects this automatically
                 if you link the services; otherwise set explicitly, e.g. https://…railway.app)
  API_KEY      — same key the iOS app uses

The script calls POST /coaching/generate/{user_id} for each user.
That endpoint is idempotent — safe to re-run if the cron fires twice.
"""

import logging
import os
import sys
import urllib.error
import urllib.request
import json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# User IDs and their approximate UTC offset for 7am wake window.
# Hardcoded for Phase 1 per T-09 ("hardcode each user's TZ").
USERS = [
    {"id": "seth", "tz": "America/New_York"},   # 7am ET = 12:00 UTC (EST) / 11:00 UTC (EDT)
]


def _generate(backend_url: str, api_key: str, user_id: str) -> bool:
    url = f"{backend_url.rstrip('/')}/coaching/generate/{user_id}"
    req = urllib.request.Request(
        url,
        data=b"",
        headers={
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            log.info("Generated coaching for user=%s (generated_at=%s)", user_id, body.get("generated_at"))
            return True
    except urllib.error.HTTPError as e:
        log.error("HTTP %s generating coaching for user=%s: %s", e.code, user_id, e.read().decode("utf-8", errors="replace"))
        return False
    except Exception as exc:
        log.error("Error generating coaching for user=%s: %s", user_id, exc)
        return False


def main():
    backend_url = os.environ.get("BACKEND_URL")
    api_key = os.environ.get("API_KEY")

    if not backend_url:
        log.error("BACKEND_URL env var is required.")
        sys.exit(1)
    if not api_key:
        log.error("API_KEY env var is required.")
        sys.exit(1)

    failures = 0
    for user in USERS:
        log.info("Running morning job for user=%s (tz=%s)", user["id"], user["tz"])
        if not _generate(backend_url, api_key, user["id"]):
            failures += 1

    if failures:
        log.error("%d/%d users failed. Check logs above.", failures, len(USERS))
        sys.exit(1)

    log.info("Morning job complete — %d/%d users succeeded.", len(USERS) - failures, len(USERS))


if __name__ == "__main__":
    main()
