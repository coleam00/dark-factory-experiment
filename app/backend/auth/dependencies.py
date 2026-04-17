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

from fastapi import Cookie, HTTPException, status

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
