"""
Tests for search_conversations repository function.

NOTE: Tests require a real Postgres instance with the full Alembic schema.
Skipped pending that environment; un-skip and fill in real fixtures when the
test-Postgres environment is available (see CLAUDE.md §Testing — snapshot DB).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

pytestmark = pytest.mark.skip(
    reason="Tests require a real Postgres instance; pending test-environment setup."
)

from backend.db.repository import (  # noqa: E402
    create_conversation,
    create_video,
    search_conversations,
    search_videos_admin,
)


# ── Title search (ported from the old search_conversations_by_title tests) ───


async def test_search_conversations_by_title_case_insensitive():
    """Title search should be case-insensitive via ILIKE."""
    user_id = str(uuid4())
    await create_conversation(user_id=user_id, title="Python Tutorial")
    await create_conversation(user_id=user_id, title="JavaScript Guide")
    await create_conversation(user_id=user_id, title="python advanced")

    results = await search_conversations(user_id, query="python")
    titles = {r["title"] for r in results}
    assert titles == {"Python Tutorial", "python advanced"}

    results = await search_conversations(user_id, query="PYTHON")
    titles = {r["title"] for r in results}
    assert titles == {"Python Tutorial", "python advanced"}


async def test_search_conversations_returns_only_own():
    """Search should only return conversations owned by the requesting user."""
    alice_id = str(uuid4())
    bob_id = str(uuid4())

    await create_conversation(user_id=alice_id, title="Alice Searchable")
    await create_conversation(user_id=bob_id, title="Bob Searchable")

    results = await search_conversations(alice_id, query="searchable")
    titles = {r["title"] for r in results}
    assert titles == {"Alice Searchable"}
    assert "Bob Searchable" not in titles


async def test_search_conversations_empty_query_returns_all():
    """Empty/blank query should return all conversations for the user (no title filter)."""
    user_id = str(uuid4())
    await create_conversation(user_id=user_id, title="Test Chat")

    results = await search_conversations(user_id, query="")
    assert isinstance(results, list)
    assert any(r["title"] == "Test Chat" for r in results)


async def test_search_conversations_none_query_returns_all():
    """None query should return all conversations for the user (no title filter)."""
    user_id = str(uuid4())
    await create_conversation(user_id=user_id, title="Test Chat 2")

    results = await search_conversations(user_id, query=None)
    assert isinstance(results, list)
    assert any(r["title"] == "Test Chat 2" for r in results)


async def test_search_conversations_limit():
    """Search should respect the limit parameter."""
    user_id = str(uuid4())
    for i in range(5):
        await create_conversation(user_id=user_id, title=f"Chat {i}")

    results = await search_conversations(user_id, query="chat", limit=3)
    assert len(results) == 3


async def test_search_conversations_no_matches():
    """Search should return an empty list when no conversations match."""
    user_id = str(uuid4())
    await create_conversation(user_id=user_id, title="Python Tutorial")

    results = await search_conversations(user_id, query="javascript")
    assert results == []


# ── Date range filtering ──────────────────────────────────────────────────────


async def test_search_conversations_date_from_filters_older():
    """Conversations created before date_from should not appear."""
    user_id = str(uuid4())
    now = datetime.now(UTC)

    old_conv = await create_conversation(user_id=user_id, title="Old")
    # Back-date the old conversation (requires direct DB update, shown here as intent)
    # ... seed old_conv with created_at = now - 10 days ...
    new_conv = await create_conversation(user_id=user_id, title="New")

    cutoff = now - timedelta(days=5)
    results = await search_conversations(user_id, date_from=cutoff)
    titles = {r["title"] for r in results}
    assert "New" in titles
    assert "Old" not in titles


async def test_search_conversations_date_to_filters_newer():
    """Conversations created after date_to should not appear."""
    user_id = str(uuid4())
    now = datetime.now(UTC)

    old_conv = await create_conversation(user_id=user_id, title="Old")
    new_conv = await create_conversation(user_id=user_id, title="New")
    # Back-date old_conv and forward-date new_conv in the DB ...

    cutoff = now - timedelta(days=5)
    results = await search_conversations(user_id, date_to=cutoff)
    titles = {r["title"] for r in results}
    assert "Old" in titles
    assert "New" not in titles


async def test_search_conversations_date_range_combined_with_title():
    """Title query + date range should both apply (AND semantics)."""
    user_id = str(uuid4())
    now = datetime.now(UTC)

    # Three conversations: two named "Python", one old, one recent; one named "JavaScript" recent
    # Seed with appropriate created_at values ...

    cutoff = now - timedelta(days=5)
    results = await search_conversations(user_id, query="python", date_from=cutoff)
    # Only the recent Python conversation should appear.
    assert all("python" in r["title"].lower() for r in results)


# ── Video ID filtering ────────────────────────────────────────────────────────


async def test_search_conversations_video_id_only():
    """Filter by video_id returns only conversations whose messages reference that video."""
    user_id = str(uuid4())
    video_id_a = "vid_aaa"
    video_id_b = "vid_bbb"

    conv_with_a = await create_conversation(user_id=user_id, title="About A")
    conv_with_b = await create_conversation(user_id=user_id, title="About B")
    # Seed messages with sources=[{"video_id": video_id_a}] for conv_with_a
    # Seed messages with sources=[{"video_id": video_id_b}] for conv_with_b
    # ...

    results = await search_conversations(user_id, video_id=video_id_a)
    titles = {r["title"] for r in results}
    assert "About A" in titles
    assert "About B" not in titles


async def test_search_conversations_video_id_combined_with_title_and_date():
    """title + date_from + video_id should all apply simultaneously."""
    user_id = str(uuid4())
    now = datetime.now(UTC)
    target_video_id = "vid_target"
    cutoff = now - timedelta(days=7)

    # Seed several conversations covering all combinations of (title match / mismatch),
    # (in-window / outside-window), (references target video / does not).
    # Only the intersection (matching title, in window, references target video) should appear.
    # ...

    results = await search_conversations(
        user_id,
        query="target",
        date_from=cutoff,
        video_id=target_video_id,
    )
    assert isinstance(results, list)
    # Each result must reference the target video and match "target" in title.


async def test_search_conversations_no_filters_returns_all_ordered_newest_first():
    """No filters should return all conversations ordered updated_at DESC."""
    user_id = str(uuid4())
    await create_conversation(user_id=user_id, title="First")
    await create_conversation(user_id=user_id, title="Second")
    await create_conversation(user_id=user_id, title="Third")

    results = await search_conversations(user_id)
    assert len(results) >= 3
    # Verify descending order
    for i in range(len(results) - 1):
        assert results[i]["updated_at"] >= results[i + 1]["updated_at"]


# ── video admin search (unchanged behavior) ──────────────────────────────────


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
