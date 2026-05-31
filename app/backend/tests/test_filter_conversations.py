"""
Tests for the filtered conversation search (issue #294): combine title text,
date range, and video filters, newest-first, user-scoped.

NOTE: Like the other repository tests, these need a real test Postgres — the
video filter relies on `jsonb_array_elements` over `messages.sources`, which has
no SQLite analogue. Skipped pending the asyncpg/Alembic test harness, mirroring
`test_search_conversations.py`.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

pytestmark = pytest.mark.skip(
    reason="Tests require a real test Postgres (jsonb_array_elements); pending harness."
)

from backend.db.repository import (  # noqa: E402
    create_conversation,
    create_message,
    list_conversation_videos,
    search_conversations,
)


async def _conv_with_video(user_id: str, title: str, video_id: str, video_title: str) -> str:
    conv = await create_conversation(user_id=user_id, title=title)
    await create_message(
        conversation_id=conv["id"],
        user_id=user_id,
        role="assistant",
        content="answer",
        sources=[{"video_id": video_id, "video_title": video_title}],
    )
    return conv["id"]


async def test_search_no_filters_returns_all_user_conversations():
    user_id = str(uuid4())
    await create_conversation(user_id=user_id, title="One")
    await create_conversation(user_id=user_id, title="Two")

    results = await search_conversations(user_id)
    assert {r["title"] for r in results} == {"One", "Two"}
    # preview column is present (matches the Conversation interface)
    assert all("preview" in r for r in results)


async def test_search_filters_by_title():
    user_id = str(uuid4())
    await create_conversation(user_id=user_id, title="Python basics")
    await create_conversation(user_id=user_id, title="Rust basics")

    results = await search_conversations(user_id, q="python")
    assert {r["title"] for r in results} == {"Python basics"}


async def test_search_filters_by_video():
    user_id = str(uuid4())
    target = await _conv_with_video(user_id, "About Docker", "vid-docker", "Docker 101")
    await _conv_with_video(user_id, "About K8s", "vid-k8s", "Kubernetes 101")

    results = await search_conversations(user_id, video_id="vid-docker")
    assert [r["id"] for r in results] == [target]


async def test_search_combines_title_and_video():
    user_id = str(uuid4())
    match = await _conv_with_video(user_id, "Docker deep dive", "vid-docker", "Docker 101")
    # Same video, non-matching title.
    await _conv_with_video(user_id, "Random chat", "vid-docker", "Docker 101")
    # Matching title, different video.
    await _conv_with_video(user_id, "Docker notes", "vid-other", "Other")

    results = await search_conversations(user_id, q="deep dive", video_id="vid-docker")
    assert [r["id"] for r in results] == [match]


async def test_search_scoped_to_user():
    alice = str(uuid4())
    bob = str(uuid4())
    await create_conversation(user_id=alice, title="Alice chat")
    await create_conversation(user_id=bob, title="Bob chat")

    results = await search_conversations(alice)
    assert {r["title"] for r in results} == {"Alice chat"}


async def test_search_orders_newest_first():
    user_id = str(uuid4())
    await create_conversation(user_id=user_id, title="First")
    await create_conversation(user_id=user_id, title="Second")

    results = await search_conversations(user_id)
    # updated_at DESC — most recently created/updated comes first.
    assert results[0]["updated_at"] >= results[-1]["updated_at"]


async def test_list_conversation_videos_dedupes_and_scopes():
    user_id = str(uuid4())
    await _conv_with_video(user_id, "Chat A", "vid-1", "Video One")
    await _conv_with_video(user_id, "Chat B", "vid-1", "Video One")  # duplicate video
    await _conv_with_video(user_id, "Chat C", "vid-2", "Video Two")

    other = str(uuid4())
    await _conv_with_video(other, "Other chat", "vid-3", "Video Three")

    videos = await list_conversation_videos(user_id)
    pairs = {(v["video_id"], v["video_title"]) for v in videos}
    assert pairs == {("vid-1", "Video One"), ("vid-2", "Video Two")}
