"""
JWT encode / decode.

Token shape:
    {"sub": "<user_uuid>", "iat": <epoch>, "exp": <epoch + 7 days>}

Signed with HS256 + JWT_SECRET. Rotated on every login (new iat/exp each time).
"""

from __future__ import annotations

import time
from typing import Any

import jwt

from backend.config import JWT_ALGORITHM, JWT_EXPIRY_SECONDS, JWT_SECRET


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired, or signed with a bad secret."""


def encode_token(user_id: str) -> str:
    """Create a signed JWT for the given user UUID."""
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET is not configured; refusing to mint tokens.")
    now = int(time.time())
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + JWT_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Verify signature + expiry and return the decoded payload. Raises TokenError on failure."""
    if not JWT_SECRET:
        raise TokenError("JWT_SECRET is not configured")
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Invalid token") from exc
