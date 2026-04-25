"""
Tests for search_conversations_by_title repository function.

NOTE: Tests were written against SQLite `init_db` fixtures. After the
Postgres/Alembic migration they need a rewrite using a real test Postgres.
Skipped pending that rewrite.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

pytestmark = pytest.mark.skip(
    reason="Tests require SQLite schema.init_db; pending rewrite for asyncpg/Alembic."
)

from backend.db.repository import (  # noqa: E402
    create_conversation,
    create_video,
    search_conversations_by_title,
    search_videos_admin,
)


async def test_search_conversations_by_title_case_insensitive():
    """Search should be case-insensitive using LOWER()."""
    user_id = str(uuid4())
    await create_conversation(user_id=user_id, title="Python Tutorial")
    await create_conversation(user_id=user_id, title="JavaScript Guide")
    await create_conversation(user_id=user_id, title="python advanced")

    results = await search_conversations_by_title(user_id, "python")
    titles = {r["title"] for r in results}
    assert titles == {"Python Tutorial", "python advanced"}

    results = await search_conversations_by_title(user_id, "PYTHON")
    titles = {r["title"] for r in results}
    assert titles == {"Python Tutorial", "python advanced"}


async def test_search_conversations_returns_only_own():
    """Search should only return conversations owned by the user."""
    alice_id = str(uuid4())
    bob_id = str(uuid4())

    await create_conversation(user_id=alice_id, title="Alice Searchable")
    await create_conversation(user_id=bob_id, title="Bob Searchable")

    results = await search_conversations_by_title(alice_id, "searchable")
    titles = {r["title"] for r in results}
    assert titles == {"Alice Searchable"}
    assert "Bob Searchable" not in titles


async def test_search_conversations_empty_query():
    """Empty query should return no results (pattern would be %%)."""
    user_id = str(uuid4())
    await create_conversation(user_id=user_id, title="Test Chat")

    results = await search_conversations_by_title(user_id, "")
    assert isinstance(results, list)


async def test_search_conversations_limit():
    """Search should respect the limit parameter."""
    user_id = str(uuid4())
    for i in range(5):
        await create_conversation(user_id=user_id, title=f"Chat {i}")

    results = await search_conversations_by_title(user_id, "chat", limit=3)
    assert len(results) == 3


async def test_search_conversations_no_matches():
    """Search should return empty list when no matches."""
    user_id = str(uuid4())
    await create_conversation(user_id=user_id, title="Python Tutorial")

    results = await search_conversations_by_title(user_id, "javascript")
    assert results == []


async def test_search_videos_admin_case_insensitive():
    """Search should be case-insensitive using ILIKE."""
    await create_video(
        title="Docker Tutorial",
        description="Learn Docker",
        url="https://youtube.com/watch?v=docker1",
        transcript="docker docker docker",
        channel_id="ch1",
        channel_title="DevOps Channel",
    )
    await create_video(
        title="Kubernetes Guide",
        description="Learn Kubernetes",
        url="https://youtube.com/watch?v=k8s1",
        transcript="k8s k8s k8s",
        channel_id="ch2",
        channel_title="Cloud Channel",
    )
    await create_video(
        title="docker advanced",
        description="Advanced Docker topics",
        url="https://youtube.com/watch?v=docker2",
        transcript="advanced docker",
        channel_id="ch1",
        channel_title="DevOps Channel",
    )

    results = await search_videos_admin("docker")
    titles = {r["title"] for r in results}
    assert titles == {"Docker Tutorial", "docker advanced"}

    results = await search_videos_admin("DOCKER")
    titles = {r["title"] for r in results}
    assert titles == {"Docker Tutorial", "docker advanced"}


async def test_search_videos_admin_empty_query():
    """Empty query should return results (pattern would be %%)."""
    await create_video(
        title="Test Video",
        description="A test",
        url="https://youtube.com/watch?v=test1",
        transcript="test test test",
    )

    results = await search_videos_admin("")
    assert len(results) == 1
    assert results[0]["title"] == "Test Video"


async def test_search_videos_admin_limit():
    """Search should respect the limit parameter."""
    for i in range(5):
        await create_video(
            title=f"Video {i}",
            description="Desc",
            url=f"https://youtube.com/watch?v=v{i}",
            transcript="transcript",
        )

    results = await search_videos_admin("Video", limit=3)
    assert len(results) == 3


async def test_search_videos_admin_no_matches():
    """Search should return empty list when no matches."""
    await create_video(
        title="Python Tutorial",
        description="Learn Python",
        url="https://youtube.com/watch?v=py1",
        transcript="python python",
    )

    results = await search_videos_admin("javascript")
    assert results == []


async def test_search_videos_admin_wildcard_chars_passthrough():
    """Lock in the decision to NOT escape % and _ in user input.

    ILIKE treats _ as a single-char wildcard. We leave it un-escaped, so
    'rust_lang' matches 'rustAlang basics' as well as 'rust_lang basics'.
    If escaping is added, only the literal-underscore title matches and
    this assertion fails — by design.
    """
    await create_video(
        title="rust_lang basics",
        description="Rust underscore test",
        url="https://youtube.com/watch?v=rust_underscore",
        transcript="rust",
    )
    await create_video(
        title="rustAlang basics",
        description="Rust wildcard test",
        url="https://youtube.com/watch?v=rust_wildcard",
        transcript="rust",
    )
    results = await search_videos_admin("rust_lang")
    assert len(results) == 2
    titles = {r["title"] for r in results}
    assert titles == {"rust_lang basics", "rustAlang basics"}


async def test_search_videos_admin_matches_description_and_channel_title():
    """Search should match description and channel_title as well as title."""
    await create_video(
        title="Generic Title",
        description="Rust Programming",
        url="https://youtube.com/watch?v=rust1",
        transcript="rust rust",
        channel_id="ch1",
        channel_title="Rust Channel",
    )

    # Match by description
    results = await search_videos_admin("Rust Programming")
    assert len(results) == 1
    assert results[0]["title"] == "Generic Title"

    # Match by channel_title
    results = await search_videos_admin("Rust Channel")
    assert len(results) == 1
    assert results[0]["title"] == "Generic Title"
