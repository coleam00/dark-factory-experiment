"""
Signup rate-limiter (issue #54).

Two independent sliding windows protect `POST /api/auth/signup`:

- **Per-IP**: 1 accepted signup per IP per hour (PER_IP_LIMIT / PER_IP_WINDOW_SECONDS)
- **Global**: 25 signup attempts per 10 minutes across the whole service
  (GLOBAL_LIMIT / GLOBAL_WINDOW_SECONDS)

Both limits return HTTP 429 with a structured body. Per-IP is checked first —
it's the more specific, more actionable error for a real user who clicked
twice. Global only fires under coordinated abuse.

Constants are **hardcoded**. No environment variable, no config override. If
a PR touches these values it must be reviewed by a human — see
FACTORY_RULES.md §2.7 and the protected-paths list in CLAUDE.md. A structural
test asserts this module's source contains no environment-variable lookups.

### Trust boundary

The route handler passes `request.client.host` as `ip`. In production, uvicorn
is started with `--proxy-headers --forwarded-allow-ips="*"`, so Starlette has
already rewritten `request.client` to the left-most `X-Forwarded-For` value
set by Caddy. This is safe because the app containers do not publish a host
port — only Caddy can reach uvicorn on the internal Docker network. If anyone
publishes `ports: - "8000:8000"` on the app service, IP spoofing becomes
possible and this assumption breaks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from backend.db import signup_attempts_repo

# Hardcoded — do not change. Any adjustment requires a human-reviewed PR.
PER_IP_WINDOW_SECONDS: int = 3600  # 1 hour
PER_IP_LIMIT: int = 1
GLOBAL_WINDOW_SECONDS: int = 600  # 10 minutes
GLOBAL_LIMIT: int = 25


Scope = Literal["ip", "global"]


class SignupRateLimited(Exception):
    """Raised by `check` when the caller should be rejected with 429."""

    def __init__(self, scope: Scope, message: str):
        self.scope: Scope = scope
        self.message: str = message
        super().__init__(f"signup_rate_limited[{scope}]: {message}")


# Human-facing copy rendered verbatim by the frontend.
_IP_MESSAGE = (
    "Only one signup per hour from this network. "
    "Please try again later or log in if you already have an account."
)
_GLOBAL_MESSAGE = "We're receiving a lot of signups right now. Please try again in a few minutes."


def _now() -> datetime:
    """Wall-clock now in UTC. Monkeypatched by tests that want to freeze time."""
    return datetime.now(UTC)


# Stable signed-bigint key for pg_advisory_xact_lock.
# Serialises signup attempts globally so two concurrent requests can't both
# see count=24 and both insert (which would let 26 through the global cap).
# Value = hash("dynachat:signup_rate_limit") folded into int64 space;
# computed once at import time from a literal, no runtime config involved.
_ADVISORY_LOCK_KEY: int = 0x53_69_67_6E_55_70_52_4C  # "SignUpRL" as bytes
if _ADVISORY_LOCK_KEY >= 1 << 63:
    _ADVISORY_LOCK_KEY -= 1 << 64


async def check(ip: str, conn) -> None:
    """Raise `SignupRateLimited` if this request should be rejected.

    Does NOT insert — the caller records the attempt explicitly so the
    audit row captures the final outcome (accepted / duplicate / etc.).

    Takes the advisory lock on the caller's connection (which must already
    be inside `conn.transaction()`). The lock releases on transaction end.
    """
    await conn.execute("SELECT pg_advisory_xact_lock($1)", _ADVISORY_LOCK_KEY)

    ip_count = await signup_attempts_repo.count_for_ip_in_window(
        conn, ip, window_seconds=PER_IP_WINDOW_SECONDS
    )
    if ip_count >= PER_IP_LIMIT:
        raise SignupRateLimited(scope="ip", message=_IP_MESSAGE)

    global_count = await signup_attempts_repo.count_global_in_window(
        conn, window_seconds=GLOBAL_WINDOW_SECONDS
    )
    if global_count >= GLOBAL_LIMIT:
        raise SignupRateLimited(scope="global", message=_GLOBAL_MESSAGE)


async def record(
    conn,
    ip: str,
    email_attempted: str | None,
    outcome: str,
) -> None:
    """Append one row to the audit table.

    Thin pass-through to keep the route handler's import surface small and
    match `rate_limit.check_and_record`'s public-API feel.
    """
    await signup_attempts_repo.insert_attempt(
        conn, ip=ip, email_attempted=email_attempted, outcome=outcome
    )
