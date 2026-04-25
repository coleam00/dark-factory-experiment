"""Circle Admin v2 API client for paid-membership verification.

Used by `routes/auth.py` to set the `users.is_member` flag on signup, login,
and `/me` refresh. Per issue #147 spec:

- 5-second timeout (Circle API is fast; long waits would block auth flow).
- **Fail-closed**: any error — timeout, network failure, non-2xx response,
  unexpected JSON shape, missing config — returns False. Users default to
  non-member status; the next /me refresh retries.
- The function never raises; callers can call it unconditionally.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.config import CIRCLE_ADMIN_TOKEN, CIRCLE_PAID_ACCESS_GROUP_ID

logger = logging.getLogger(__name__)

CIRCLE_BASE = "https://app.circle.so/api/admin/v2"
TIMEOUT_SECONDS = 5.0


async def verify_paid_member(email: str) -> bool:
    """Return True iff *email* belongs to an active Circle member in the paid access group.

    Fail-closed: any error path returns False with a warning log. Callers should
    rely on this never throwing.
    """
    if not CIRCLE_ADMIN_TOKEN or not CIRCLE_PAID_ACCESS_GROUP_ID:
        # Config not set — silently treat everyone as non-member. The startup
        # warning in config.py already flagged this once.
        return False

    if not email:
        return False

    headers = {
        "Authorization": f"Token {CIRCLE_ADMIN_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers=headers) as client:
            # Step 1: look up the member by email
            r = await client.get(
                f"{CIRCLE_BASE}/community_members/search",
                params={"email": email},
            )
            if r.status_code == 404:
                # Not a Circle member at all — ordinary case, no log spam.
                return False
            if r.status_code != 200:
                logger.warning(
                    "Circle member search returned %s for email; fail-closed",
                    r.status_code,
                )
                return False

            member = _extract_member(r.json())
            if member is None:
                return False
            if not member.get("active", True):
                # Inactive members lose access immediately.
                return False
            member_id = member.get("id")
            if not member_id:
                logger.warning("Circle member search response missing id; fail-closed")
                return False

            # Step 2: confirm the member is in the paid access group
            r2 = await client.get(
                f"{CIRCLE_BASE}/community_members/{member_id}/access_groups"
            )
            if r2.status_code != 200:
                logger.warning(
                    "Circle access_groups returned %s for member %s; fail-closed",
                    r2.status_code,
                    member_id,
                )
                return False

            groups = r2.json().get("records") or []
            return any(int(g.get("id", 0)) == CIRCLE_PAID_ACCESS_GROUP_ID for g in groups)

    except httpx.TimeoutException:
        logger.warning("Circle API timed out verifying email; fail-closed")
        return False
    except httpx.HTTPError as exc:
        logger.warning("Circle API HTTP error verifying email: %s; fail-closed", exc)
        return False
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("Circle API response shape unexpected: %s; fail-closed", exc)
        return False


def _extract_member(body: Any) -> dict[str, Any] | None:
    """Normalize the search response shape.

    Circle's `/community_members/search` historically returned the member object
    directly. Newer responses sometimes wrap in `records`. Handle both.
    """
    if isinstance(body, dict):
        if "id" in body:
            return body
        recs = body.get("records") if isinstance(body.get("records"), list) else None
        if recs:
            first = recs[0]
            if isinstance(first, dict):
                return first
    return None
