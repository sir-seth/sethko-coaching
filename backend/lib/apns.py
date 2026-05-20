"""
APNs HTTP/2 client — JWT token-based auth (p8 key).

New dep flagged per CLAUDE.md: httpx[http2] for HTTP/2 transport.
PyJWT is already present (T-26). This avoids the aioapns persistent-connection
model, which is ill-suited to cron jobs that connect, send, and exit.

Required env vars:
  APNS_KEY_ID    — 10-char key ID from Apple Developer portal
  APNS_TEAM_ID   — 10-char team ID
  APNS_AUTH_KEY  — contents of the .p8 file (newlines preserved)
  APNS_BUNDLE_ID — app bundle ID (default: com.sethko.coaching)
"""

import logging
import os
import time
from typing import Optional

import jwt

log = logging.getLogger(__name__)

_APNS_PROD    = "https://api.push.apple.com"
_APNS_SANDBOX = "https://api.sandbox.push.apple.com"

# Cache the JWT so we don't regenerate it on every call (valid 1h, refresh at 50 min).
_jwt_cache: dict = {}


def _bearer() -> str:
    global _jwt_cache
    now = time.time()
    if _jwt_cache.get("exp", 0) > now + 600:
        return _jwt_cache["token"]

    key_id  = os.environ["APNS_KEY_ID"]
    team_id = os.environ["APNS_TEAM_ID"]
    auth_key = os.environ["APNS_AUTH_KEY"]

    token = jwt.encode(
        {"iss": team_id, "iat": int(now)},
        auth_key,
        algorithm="ES256",
        headers={"alg": "ES256", "kid": key_id},
    )
    _jwt_cache = {"token": token, "exp": now + 2700}  # refresh every 45 min
    return token


async def send_push(
    *,
    device_token: str,
    env: str,
    title: str,
    body: str,
    category: str,
    collapse_id: Optional[str] = None,
) -> bool:
    """Send an APNs alert push. Returns True on 200, False on any failure."""
    try:
        import httpx
    except ImportError:
        log.error("httpx not installed — cannot send push. Add httpx[http2] to requirements.")
        return False

    bundle_id = os.environ.get("APNS_BUNDLE_ID", "com.sethko.coaching")
    host = _APNS_PROD if env == "production" else _APNS_SANDBOX
    url  = f"{host}/3/device/{device_token}"

    # Truncate body at 90 grapheme clusters (lock-screen truncation guard).
    body_safe = _truncate_graphemes(body, 90)

    payload = {
        "aps": {
            "alert":    {"title": title, "body": body_safe},
            "sound":    "default",
            "category": category,
        }
    }

    headers = {
        "authorization": f"bearer {_bearer()}",
        "apns-topic":    bundle_id,
        "apns-push-type": "alert",
        "apns-priority": "10",
        "apns-expiration": "0",
    }
    if collapse_id:
        headers["apns-collapse-id"] = collapse_id

    try:
        async with httpx.AsyncClient(http2=True, timeout=10) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code == 200:
            log.info("APNs push sent token=…%s category=%s", device_token[-6:], category)
            return True
        reason = "unknown"
        if resp.content:
            try:
                reason = resp.json().get("reason", "unknown")
            except Exception:
                pass
        log.warning(
            "APNs rejected token=…%s status=%d reason=%s",
            device_token[-6:], resp.status_code, reason,
        )
        return False
    except Exception as exc:
        log.error("APNs send error: %s", exc)
        return False


def _truncate_graphemes(text: str, limit: int) -> str:
    """Truncate to at most `limit` grapheme clusters (not bytes, not code points)."""
    import unicodedata
    clusters: list[str] = []
    current = ""
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith("M") or cat == "Cf":
            current += ch
        else:
            if current:
                clusters.append(current)
            current = ch
    if current:
        clusters.append(current)
    if len(clusters) <= limit:
        return text
    return "".join(clusters[:limit - 1]) + "…"
