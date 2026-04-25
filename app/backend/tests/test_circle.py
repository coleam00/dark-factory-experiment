"""Unit tests for backend.integrations.circle.verify_paid_member.

The function MUST never raise — it always returns a bool. We exercise:
- happy path (active member in the paid access group)
- 404 from member search (non-member)
- non-200 from member search (fail-closed)
- non-200 from access_groups (fail-closed)
- timeout (fail-closed)
- inactive member
- member exists but NOT in paid access group
- empty / malformed config
- malformed response shape
"""

from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

import httpx
import pytest
import respx

from backend.integrations import circle


@pytest.fixture(autouse=True)
def _circle_config(monkeypatch: pytest.MonkeyPatch):
    """Stamp Circle config onto the module so the function actually runs.

    Done via monkeypatch (not env vars) because config.py reads the env once at
    import time, well before this test file is loaded.
    """
    monkeypatch.setattr(circle, "CIRCLE_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(circle, "CIRCLE_PAID_ACCESS_GROUP_ID", 16841)


@respx.mock
async def test_active_member_in_paid_group_returns_true():
    respx.get("https://app.circle.so/api/admin/v2/community_members/search").mock(
        return_value=httpx.Response(200, json={"id": 999, "active": True})
    )
    respx.get("https://app.circle.so/api/admin/v2/community_members/999/access_groups").mock(
        return_value=httpx.Response(200, json={"records": [{"id": 16841}]})
    )

    assert await circle.verify_paid_member("paid@example.com") is True


@respx.mock
async def test_member_not_in_paid_group_returns_false():
    respx.get("https://app.circle.so/api/admin/v2/community_members/search").mock(
        return_value=httpx.Response(200, json={"id": 999, "active": True})
    )
    respx.get("https://app.circle.so/api/admin/v2/community_members/999/access_groups").mock(
        return_value=httpx.Response(200, json={"records": [{"id": 53474}]})  # different group
    )

    assert await circle.verify_paid_member("free@example.com") is False


@respx.mock
async def test_member_search_404_returns_false():
    respx.get("https://app.circle.so/api/admin/v2/community_members/search").mock(
        return_value=httpx.Response(404, text="Not found")
    )

    assert await circle.verify_paid_member("notmember@example.com") is False


@respx.mock
async def test_member_search_500_fails_closed():
    respx.get("https://app.circle.so/api/admin/v2/community_members/search").mock(
        return_value=httpx.Response(500, text="Internal error")
    )

    assert await circle.verify_paid_member("anyone@example.com") is False


@respx.mock
async def test_access_groups_500_fails_closed():
    respx.get("https://app.circle.so/api/admin/v2/community_members/search").mock(
        return_value=httpx.Response(200, json={"id": 999, "active": True})
    )
    respx.get("https://app.circle.so/api/admin/v2/community_members/999/access_groups").mock(
        return_value=httpx.Response(503, text="Bad gateway")
    )

    assert await circle.verify_paid_member("paid@example.com") is False


@respx.mock
async def test_timeout_fails_closed():
    respx.get("https://app.circle.so/api/admin/v2/community_members/search").mock(
        side_effect=httpx.TimeoutException("timeout")
    )

    assert await circle.verify_paid_member("any@example.com") is False


@respx.mock
async def test_inactive_member_returns_false():
    respx.get("https://app.circle.so/api/admin/v2/community_members/search").mock(
        return_value=httpx.Response(200, json={"id": 999, "active": False})
    )

    assert await circle.verify_paid_member("inactive@example.com") is False


@respx.mock
async def test_records_wrapped_response_handled():
    """Some Circle endpoints wrap a single record in `records: [...]`."""
    respx.get("https://app.circle.so/api/admin/v2/community_members/search").mock(
        return_value=httpx.Response(
            200, json={"records": [{"id": 999, "active": True}]}
        )
    )
    respx.get("https://app.circle.so/api/admin/v2/community_members/999/access_groups").mock(
        return_value=httpx.Response(200, json={"records": [{"id": 16841}]})
    )

    assert await circle.verify_paid_member("paid@example.com") is True


@respx.mock
async def test_malformed_search_response_fails_closed():
    respx.get("https://app.circle.so/api/admin/v2/community_members/search").mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )

    assert await circle.verify_paid_member("paid@example.com") is False


async def test_empty_email_returns_false():
    assert await circle.verify_paid_member("") is False


async def test_missing_config_returns_false(monkeypatch: pytest.MonkeyPatch):
    """If the token isn't configured, verify_paid_member shorts to False."""
    monkeypatch.setattr(circle, "CIRCLE_ADMIN_TOKEN", "")

    assert await circle.verify_paid_member("paid@example.com") is False
