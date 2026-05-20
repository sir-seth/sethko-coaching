"""Shared auth dependencies — imported by main.py and all route modules."""

import os
from fastapi import Header, HTTPException, Request, status


def require_api_key(x_api_key: str = Header(...)):
    """Legacy shared secret — used by cron/internal routes only."""
    expected = os.environ.get("API_KEY")
    if not expected:
        raise HTTPException(status_code=500, detail="API_KEY not configured on server.")
    if x_api_key != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key.")


def get_current_user_id(request: Request) -> str:
    """Read user_id from request.state (set by SessionMiddleware in main.py)."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return user_id
