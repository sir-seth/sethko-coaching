"""
Apple Sign In with Apple (SIWA) identity token verification.

Token flow:
  1. iOS generates a random raw_nonce (32 chars).
  2. iOS SHA-256 hashes the raw_nonce and passes the hash to ASAuthorizationController.
  3. Apple embeds the hash in the JWT's `nonce` claim.
  4. iOS sends the raw_nonce (un-hashed) to this backend.
  5. Backend SHA-256 hashes raw_nonce and compares to token's `nonce` claim.

Apple's public keys are fetched from JWKS and cached for 24h.
"""

import asyncio
import hashlib
import json
import logging
import os
import time
import urllib.request
from typing import Optional

import jwt
from jwt.algorithms import RSAAlgorithm

log = logging.getLogger(__name__)

_JWKS_URL = "https://appleid.apple.com/auth/keys"
_ISSUER = "https://appleid.apple.com"
_JWKS_TTL = 86400  # 24 hours

_jwks_cache: Optional[dict] = None
_jwks_fetched_at: float = 0


def _fetch_jwks_sync() -> dict:
    req = urllib.request.Request(_JWKS_URL, headers={"User-Agent": "SethkoCoaching/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


async def _get_jwks(force_refresh: bool = False) -> dict:
    global _jwks_cache, _jwks_fetched_at
    now = time.monotonic()
    if not force_refresh and _jwks_cache and (now - _jwks_fetched_at) < _JWKS_TTL:
        return _jwks_cache
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _fetch_jwks_sync)
    _jwks_cache = data
    _jwks_fetched_at = now
    log.info("Refreshed Apple JWKS (%d keys)", len(data.get("keys", [])))
    return data


async def verify_identity_token(token: str, raw_nonce: str) -> dict:
    """
    Verify an Apple identity token. Returns the JWT payload on success.
    Raises ValueError with a description on any verification failure.

    Checks: RS256 signature, iss == Apple, aud in allowlist, exp not past, nonce match.
    """
    aud_allowlist = [
        a.strip()
        for a in os.environ.get("APPLE_AUD_ALLOWLIST", "").split()
        if a.strip()
    ]
    if not aud_allowlist:
        raise ValueError("APPLE_AUD_ALLOWLIST env var not configured")

    try:
        header = jwt.get_unverified_header(token)
    except jwt.exceptions.DecodeError as exc:
        raise ValueError(f"Malformed token header: {exc}")

    kid = header.get("kid")
    jwks = await _get_jwks()
    key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)

    # Apple rotates keys occasionally — refresh once on miss
    if key_data is None:
        jwks = await _get_jwks(force_refresh=True)
        key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)

    if key_data is None:
        raise ValueError(f"No Apple public key found for kid={kid!r}")

    public_key = RSAAlgorithm.from_jwk(json.dumps(key_data))

    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=_ISSUER,
            audience=aud_allowlist,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise ValueError("Apple identity token has expired")
    except jwt.InvalidAudienceError:
        raise ValueError(f"Token audience not in configured allowlist")
    except jwt.exceptions.PyJWTError as exc:
        raise ValueError(f"Token verification failed: {exc}")

    # Nonce: Apple stores SHA-256(raw_nonce) in the token
    expected_nonce_hash = hashlib.sha256(raw_nonce.encode()).hexdigest()
    if payload.get("nonce") != expected_nonce_hash:
        raise ValueError("Nonce mismatch — possible replay attack")

    return payload
