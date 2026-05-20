"""
Sethko Coaching — Oura Ring integration

Handles:
  - PKCE code-verifier/challenge generation (for the connect flow)
  - Access-token refresh (Oura tokens expire after ~24 h; refresh tokens last 90 days)
  - 7-day data pull from Oura v2 API
  - Normalization to RecoverySignal (same shape as whoop.py output)

Rate limit: Oura allows 5 req/min per user. The 7-day pull is 3 calls
(sleep, readiness, activity) — well within budget. Do NOT loop per-day.

Refresh token expiry: if refresh fails with 401/400, set
users.sync_state.error = "needs_reauth" so the masthead shows a gray dot
+ Connect pill (same edge-state as "Whoop disconnected").
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from models import RecoverySignal
from lib.devices import rescale_oura_strain

log = logging.getLogger(__name__)

OURA_TOKEN_URL   = "https://api.ouraring.com/oauth/token"
OURA_AUTH_URL    = "https://cloud.ouraring.com/oauth/authorize"
OURA_API_BASE    = "https://api.ouraring.com"
OURA_SCOPES      = "daily readiness sleep heartrate workout"


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for S256 PKCE."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def build_authorize_url(code_challenge: str, state: str, redirect_uri: str) -> str:
    client_id = os.environ["OURA_CLIENT_ID"]
    params = {
        "response_type":         "code",
        "client_id":             client_id,
        "redirect_uri":          redirect_uri,
        "scope":                 OURA_SCOPES,
        "code_challenge":        code_challenge,
        "code_challenge_method": "S256",
        "state":                 state,
    }
    return f"{OURA_AUTH_URL}?{urllib.parse.urlencode(params)}"


# ---------------------------------------------------------------------------
# Token exchange + refresh
# ---------------------------------------------------------------------------

def _token_is_expired(tokens: dict) -> bool:
    expires_at = tokens.get("expires_at")
    if not expires_at:
        return True
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    return datetime.now(timezone.utc) >= (expires_at - timedelta(minutes=5))


def exchange_code(code: str, code_verifier: str, redirect_uri: str) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    client_id     = os.environ["OURA_CLIENT_ID"]
    client_secret = os.environ["OURA_CLIENT_SECRET"]

    data = urllib.parse.urlencode({
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  redirect_uri,
        "client_id":     client_id,
        "client_secret": client_secret,
        "code_verifier": code_verifier,
    }).encode("utf-8")

    req = urllib.request.Request(
        OURA_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        new_tokens = json.loads(resp.read().decode())

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=new_tokens.get("expires_in", 86400))
    return {
        "access_token":  new_tokens["access_token"],
        "refresh_token": new_tokens["refresh_token"],
        "expires_at":    expires_at.isoformat(),
    }


def refresh_if_needed(tokens: dict) -> dict:
    """Return tokens dict with a fresh access_token if the current one is expiring."""
    if not _token_is_expired(tokens):
        return tokens

    log.info("Refreshing Oura access token")

    client_id     = os.environ["OURA_CLIENT_ID"]
    client_secret = os.environ["OURA_CLIENT_SECRET"]

    data = urllib.parse.urlencode({
        "grant_type":    "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id":     client_id,
        "client_secret": client_secret,
    }).encode("utf-8")

    req = urllib.request.Request(
        OURA_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            new_tokens = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log.error("Oura token refresh failed: HTTP %s — %s", e.code, body)
        raise

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=new_tokens.get("expires_in", 86400))
    return {
        **tokens,
        "access_token":  new_tokens["access_token"],
        "refresh_token": new_tokens.get("refresh_token", tokens.get("refresh_token")),
        "expires_at":    expires_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Data pull + normalization
# ---------------------------------------------------------------------------

def _get(path: str, access_token: str, params: dict | None = None) -> dict:
    url = f"{OURA_API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        log.error("Oura API error on %s: HTTP %s — %s", path, e.code, body)
        raise


async def fetch_recent(access_token: str, days: int = 7) -> RecoverySignal | None:
    """
    Pull the last `days` days of Oura data and return a normalised
    RecoverySignal for the most recent night, or None if ring wasn't worn.

    Oura's `day` field is already in the user's local TZ date, so no
    conversion is needed for joining against subjective_log.date.
    """
    end_date   = date.today()
    start_date = end_date - timedelta(days=days)
    params = {
        "start_date": start_date.isoformat(),
        "end_date":   end_date.isoformat(),
    }

    sleep_data      = _get("/v2/usercollection/sleep", access_token, params)
    readiness_data  = _get("/v2/usercollection/daily_readiness", access_token, params)
    activity_data   = _get("/v2/usercollection/daily_activity", access_token, params)

    # Most recent night sleep document (exclude naps).
    sleep_records = [r for r in sleep_data.get("data", []) if r.get("type") != "nap"]
    sleep_records.sort(key=lambda r: r.get("day", ""), reverse=True)
    latest_sleep = sleep_records[0] if sleep_records else None

    if latest_sleep is None:
        return None

    signal_date = date.fromisoformat(latest_sleep["day"])

    # Readiness score for the same day.
    readiness_records = readiness_data.get("data", [])
    readiness_records.sort(key=lambda r: r.get("day", ""), reverse=True)
    readiness_day = next((r for r in readiness_records if r.get("day") == latest_sleep["day"]), None)
    recovery_score = readiness_day.get("score") if readiness_day else None

    # Activity score for the same day (strain proxy).
    activity_records = activity_data.get("data", [])
    activity_records.sort(key=lambda r: r.get("day", ""), reverse=True)
    activity_day = next((r for r in activity_records if r.get("day") == latest_sleep["day"]), None)
    activity_score = activity_day.get("score") if activity_day else None

    # Map sleep duration: total_sleep_duration is in seconds.
    total_sleep_s = latest_sleep.get("total_sleep_duration")
    sleep_hours   = round(total_sleep_s / 3600, 2) if total_sleep_s else None

    return RecoverySignal(
        date=signal_date,
        source="oura",
        hrv_ms=latest_sleep.get("average_hrv"),            # RMSSD-based, same unit as Whoop
        rhr_bpm=latest_sleep.get("lowest_heart_rate"),     # closest Oura equivalent to RHR
        sleep_hours=sleep_hours,
        sleep_score=latest_sleep.get("efficiency"),        # 0–100
        strain_score=rescale_oura_strain(activity_score),  # approx; see lib/devices.py
        recovery_score=recovery_score,
    )
