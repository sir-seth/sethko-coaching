"""
Sethko Coaching — Whoop integration (backend module)

Absorbed from whoop_pull.py. The OAuth dance (browser redirect) lives in
whoop_pull.py and is still run locally to get initial tokens. This module
handles everything the backend needs day-to-day:
  - Refreshing the access token when it's expired
  - Pulling recent recovery data
  - Normalising the raw Whoop response into a RecoverySignal

The initial token exchange (browser-based OAuth flow) still happens locally
via whoop_pull.py. Once you have tokens in tokens.json, migrate them to the
database with:

    python seed_whoop_tokens.py

(That script reads tokens.json and POSTs each user's tokens to the DB.)
"""

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from statistics import mean

from models import RecoverySignal

log = logging.getLogger(__name__)

WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_API_BASE = "https://api.prod.whoop.com/developer"

# See whoop_pull.py for why this UA is needed.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Safari/605.1.15"
)


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------

def _token_is_expired(tokens: dict) -> bool:
    expires_at = tokens.get("expires_at")
    if not expires_at:
        return True
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    # Refresh 5 minutes early to avoid edge cases.
    return datetime.now(timezone.utc) >= (expires_at - timedelta(minutes=5))


async def refresh_if_needed(tokens: dict) -> dict:
    """Return tokens dict with a fresh access_token if the current one is expiring."""
    if not _token_is_expired(tokens):
        return tokens

    log.info("Refreshing Whoop access token for whoop_user_id=%s", tokens.get("whoop_user_id"))

    client_id = os.environ["WHOOP_CLIENT_ID"]
    client_secret = os.environ["WHOOP_CLIENT_SECRET"]

    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")

    req = urllib.request.Request(
        WHOOP_TOKEN_URL,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            new_tokens = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log.error("Whoop token refresh failed: HTTP %s — %s", e.code, body)
        raise

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=new_tokens.get("expires_in", 3600))
    return {
        **tokens,
        "access_token": new_tokens["access_token"],
        "refresh_token": new_tokens.get("refresh_token", tokens.get("refresh_token")),
        "expires_at": expires_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Data pull + normalisation
# ---------------------------------------------------------------------------

def _get(path: str, access_token: str, params: dict = None) -> dict:
    url = f"{WHOOP_API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log.error("Whoop API error on %s: HTTP %s — %s", path, e.code, body)
        raise


def _safe_score(record: dict) -> dict | None:
    if record.get("score_state") != "SCORED":
        return None
    return record.get("score")


def _sleep_hours(score: dict) -> float | None:
    stage = score.get("stage_summary", {})
    in_bed_ms = stage.get("total_in_bed_time_milli", 0)
    awake_ms = stage.get("total_awake_time_milli", 0)
    total = (in_bed_ms - awake_ms) / 1000 / 60 / 60
    return round(total, 2) if total > 0 else None


async def fetch_recent(access_token: str, days: int = 7) -> RecoverySignal:
    """
    Pull the last `days` days of Whoop data and return a normalised
    RecoverySignal for the most recent scored day.
    """
    from datetime import date

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    params = {
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "limit": 25,
    }

    recovery_data = _get("/v2/recovery", access_token, params)
    sleep_data = _get("/v2/activity/sleep", access_token, params)
    cycle_data = _get("/v2/cycle", access_token, params)

    # Most recent scored recovery record.
    recovery_records = recovery_data.get("records", [])
    scored_recoveries = [r for r in recovery_records if _safe_score(r)]
    latest_recovery = _safe_score(scored_recoveries[0]) if scored_recoveries else {}

    # Most recent overnight sleep.
    sleep_records = sleep_data.get("records", [])
    overnight = [r for r in sleep_records if not r.get("nap")]
    scored_sleep = [r for r in overnight if _safe_score(r)]
    latest_sleep_score = _safe_score(scored_sleep[0]) if scored_sleep else {}

    # Most recent strain from cycles.
    cycle_records = cycle_data.get("records", [])
    scored_cycles = [_safe_score(r) for r in cycle_records if _safe_score(r)]
    latest_strain = scored_cycles[0].get("strain") if scored_cycles else None

    # Determine the date for this signal.
    signal_date = date.today()
    if scored_recoveries:
        created = scored_recoveries[0].get("created_at", "")
        if created:
            try:
                signal_date = datetime.fromisoformat(created.replace("Z", "+00:00")).date()
            except ValueError:
                pass

    return RecoverySignal(
        date=signal_date,
        source="whoop",
        hrv_ms=latest_recovery.get("hrv_rmssd_milli"),
        rhr_bpm=latest_recovery.get("resting_heart_rate"),
        sleep_hours=_sleep_hours(latest_sleep_score) if latest_sleep_score else None,
        sleep_score=latest_sleep_score.get("sleep_performance_percentage"),
        strain_score=latest_strain,
        recovery_score=latest_recovery.get("recovery_score"),
    )
