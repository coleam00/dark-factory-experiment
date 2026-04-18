"""
FastAPI dependencies for authenticated routes.

Usage:
    @router.post("/foo")
    async def foo(user: dict = Depends(get_current_user)):
        ...

Any protected route returns 401 automatically when the cookie is missing,
malformed, expired, or references a deleted user.
"""

from __future__ import annotations

from typing import Any

from fastapi import Cookie, Depends, HTTPException, status

from backend import config
from backend.auth.tokens import TokenError, decode_token
from backend.db import users_repo

COOKIE_NAME = "session"


async def get_current_user(session: str | None = Cookie(default=None)) -> dict[str, Any]:
    """Resolve the session cookie to a user row. 401 on any failure."""
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = decode_token(session)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token")
    user = await users_repo.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists"
        )
    return user


def is_admin_email(email: str) -> bool:
    """Return True iff *email* matches the configured admin (case-insensitive).

    Returns False when ADMIN_USER_EMAIL is unset — fail-safe so a missing config
    never grants admin by default.
    """
    # Read the attribute at call time (not import time) so tests can monkeypatch.
    configured = getattr(config, "ADMIN_USER_EMAIL", "") or ""
    if not configured:
        return False
    return email.strip().lower() == configured.strip().lower()


async def get_current_admin(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Resolve the session cookie AND require the user to be the admin.

    401 if unauthenticated (via get_current_user). 403 if authenticated but not
    the configured admin, or if ADMIN_USER_EMAIL is unset.
    """
    if not is_admin_email(str(user.get("email", ""))):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user
