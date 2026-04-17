"""
Password hashing using bcrypt directly.

bcrypt cost = 12 rounds (issue #51 design decision). Increase only via a new
MISSION-reviewed PR since hash cost is a security parameter.
"""

from __future__ import annotations

import bcrypt

BCRYPT_ROUNDS = 12


def hash_password(plaintext: str) -> str:
    """Hash a plaintext password with bcrypt. Returns UTF-8 string for DB storage."""
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(plaintext.encode("utf-8"), salt).decode("utf-8")


def verify_password(plaintext: str, password_hash: str) -> bool:
    """Constant-time verify of plaintext against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed hash — treat as failed verification, not an error.
        return False
