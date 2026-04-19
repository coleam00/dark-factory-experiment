"""
Tests for /api/admin/* endpoints.

Covers:
- 401 for unauthenticated callers (cookie missing or invalid)
- 403 for authenticated non-admin callers
- 200 for the configured admin email (server-computed is_admin hint)
- DELETE cascades chunks through FK ON DELETE CASCADE
- Re-sync preserves existing chunks if Supadata / embeddings fail
- list_videos_admin returns chunk_count via SQL subquery

Postgres/auth fixtures are reused from test_auth.py (in-memory users_repo,
stubbed pg pool, permissive rate-limit). SQLite (videos/chunks) uses a temp
DB per test to isolate admin mutations.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

# Set secrets BEFORE importing the app so config.py picks them up.
os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

pytestmark = pytest.mark.skip(
    reason="temp_db_schema fixture uses deleted SQLite schema module; pending Alembic rewrite."
)

ADMIN_EMAIL = "admin@example.com"


@pytest.fixture(autouse=True)
def set_admin_email(monkeypatch):
    """Configure ADMIN_USER_EMAIL for every test in this module.

    is_admin_email() reads the attribute at call time via getattr, so
    monkeypatching the module attribute is sufficient — no re-import needed.
    """
    from backend import config as _config

    monkeypatch.setattr(_config, "ADMIN_USER_EMAIL", ADMIN_EMAIL)


@pytest.fixture(autouse=True)
def temp_db_schema(tmp_path, monkeypatch):
    """Fresh SQLite per test — admin mutations (create/delete video) must not
    leak between tests. See test_channel_sync.py for why all three modules
    need DB_PATH patched.
    """
    db_path = str(tmp_path / "test_chat.db")
    monkeypatch.setenv("DB_PATH", db_path)

    from backend import config as _config
    from backend.db import repository as _repository
    from backend.db import schema as _schema

    monkeypatch.setattr(_config, "DB_PATH", db_path)
    monkeypatch.setattr(_schema, "DB_PATH", db_path)
    monkeypatch.setattr(_repository, "DB_PATH", db_path)

    import asyncio

    asyncio.run(_schema.init_db())
    return db_path


@pytest.fixture(autouse=True)
def fake_users_repo(monkeypatch):
    """In-memory users_repo — mirrors test_auth.py."""
    store: dict[str, dict[str, Any]] = {}

    async def create_user(email: str, password_hash: str, **kwargs: Any) -> dict[str, Any]:
        import asyncpg

        email_lower = email.lower()
        for u in store.values():
            if str(u["email"]).lower() == email_lower:
                raise asyncpg.UniqueViolationError("duplicate email")
        uid = str(uuid4())
        row = {
            "id": uid,
            "email": email,
            "password_hash": password_hash,
            "created_at": None,
            "last_login_at": None,
        }
        store[uid] = row
        return {k: v for k, v in row.items() if k != "password_hash"}

    async def get_user_by_email(email: str) -> dict[str, Any] | None:
        email_lower = email.lower()
        for u in store.values():
            if str(u["email"]).lower() == email_lower:
                return dict(u)
        return None

    async def get_user_by_id(user_id: Any) -> dict[str, Any] | None:
        u = store.get(str(user_id))
        if not u:
            return None
        return {k: v for k, v in u.items() if k != "password_hash"}

    async def update_last_login(user_id: Any) -> None:
        u = store.get(str(user_id))
        if u:
            u["last_login_at"] = "now"

    from backend.auth import dependencies as auth_deps
    from backend.db import users_repo
    from backend.routes import auth as auth_route

    for target in (users_repo, auth_deps.users_repo, auth_route.users_repo):
        monkeypatch.setattr(target, "get_user_by_id", get_user_by_id)
    monkeypatch.setattr(users_repo, "create_user", create_user)
    monkeypatch.setattr(users_repo, "get_user_by_email", get_user_by_email)
    monkeypatch.setattr(users_repo, "update_last_login", update_last_login)
    monkeypatch.setattr(auth_route.users_repo, "create_user", create_user)
    monkeypatch.setattr(auth_route.users_repo, "get_user_by_email", get_user_by_email)
    monkeypatch.setattr(auth_route.users_repo, "update_last_login", update_last_login)
    return store


@pytest.fixture(autouse=True)
def stub_pg_lifecycle(monkeypatch):
    from backend.db import postgres as pg

    async def noop():
        return None

    monkeypatch.setattr(pg, "close_pg_pool", noop)


@pytest.fixture
async def client():
    from backend.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as c:
        yield c


async def _signup(client: AsyncClient, email: str) -> None:
    r = await client.post(
        "/api/auth/signup",
        json={"email": email, "password": "password123"},
    )
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# Gating — 401 / 403 / 200
# ---------------------------------------------------------------------------


async def test_admin_videos_unauthenticated_returns_401(client):
    r = await client.get("/api/admin/videos")
    assert r.status_code == 401


async def test_admin_videos_non_admin_returns_403(client):
    await _signup(client, "regular@example.com")
    r = await client.get("/api/admin/videos")
    assert r.status_code == 403


async def test_admin_videos_admin_returns_200_empty_list(client):
    await _signup(client, ADMIN_EMAIL)
    r = await client.get("/api/admin/videos")
    assert r.status_code == 200
    assert r.json() == {"videos": []}


async def test_me_reports_is_admin_true_for_admin(client):
    await _signup(client, ADMIN_EMAIL)
    r = await client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["is_admin"] is True


async def test_me_reports_is_admin_false_for_regular(client):
    await _signup(client, "regular@example.com")
    r = await client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["is_admin"] is False


async def test_admin_email_match_is_case_insensitive(client):
    # Signed up with different case than configured; should still be admin.
    await _signup(client, ADMIN_EMAIL.upper())
    r = await client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["is_admin"] is True


# ---------------------------------------------------------------------------
# Listing with chunk counts
# ---------------------------------------------------------------------------


async def test_list_videos_admin_reports_chunk_counts(client):
    from backend.db import repository

    video = await repository.create_video(
        title="T", description="D", url="https://u/v?v=abc123", transcript="tx"
    )
    for i in range(3):
        await repository.create_chunk(
            video_id=video["id"], content=f"c{i}", embedding=[0.0] * 4, chunk_index=i
        )

    await _signup(client, ADMIN_EMAIL)
    r = await client.get("/api/admin/videos")
    assert r.status_code == 200
    videos = r.json()["videos"]
    assert len(videos) == 1
    assert videos[0]["id"] == video["id"]
    assert videos[0]["chunk_count"] == 3


# ---------------------------------------------------------------------------
# DELETE cascade
# ---------------------------------------------------------------------------


async def test_delete_video_cascades_chunks(client):
    from backend.db import repository

    video = await repository.create_video(
        title="T", description="D", url="https://u/v?v=abc", transcript="tx"
    )
    await repository.create_chunk(
        video_id=video["id"], content="c0", embedding=[0.0] * 4, chunk_index=0
    )
    await repository.create_chunk(
        video_id=video["id"], content="c1", embedding=[0.0] * 4, chunk_index=1
    )

    await _signup(client, ADMIN_EMAIL)
    r = await client.delete(f"/api/admin/videos/{video['id']}")
    assert r.status_code == 204

    assert await repository.get_video(video["id"]) is None
    assert await repository.list_chunks_for_video(video["id"]) == []


async def test_delete_unknown_video_returns_404(client):
    await _signup(client, ADMIN_EMAIL)
    r = await client.delete("/api/admin/videos/does-not-exist")
    assert r.status_code == 404


async def test_delete_non_admin_returns_403(client):
    await _signup(client, "regular@example.com")
    r = await client.delete("/api/admin/videos/any-id")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Re-sync atomicity — existing chunks survive a Supadata/embedding failure
# ---------------------------------------------------------------------------


async def test_resync_preserves_chunks_on_supadata_failure(client):
    from supadata import SupadataError

    from backend.db import repository

    video = await repository.create_video(
        title="Original",
        description="Desc",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        transcript="original transcript",
    )
    await repository.create_chunk(
        video_id=video["id"], content="original-0", embedding=[0.1] * 4, chunk_index=0
    )
    await repository.create_chunk(
        video_id=video["id"], content="original-1", embedding=[0.2] * 4, chunk_index=1
    )

    await _signup(client, ADMIN_EMAIL)

    with patch(
        "backend.routes.admin.fetch_video_for_ingest",
        new=AsyncMock(
            side_effect=SupadataError(error="rate_limited", message="rate-limited", details="")
        ),
    ):
        r = await client.post(f"/api/admin/videos/{video['id']}/re-sync")

    assert r.status_code == 503
    chunks = await repository.list_chunks_for_video(video["id"])
    assert [c["content"] for c in chunks] == ["original-0", "original-1"]


async def test_resync_replaces_chunks_on_success(client):
    from backend.db import repository

    video = await repository.create_video(
        title="Original",
        description="Desc",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        transcript="original transcript",
    )
    await repository.create_chunk(
        video_id=video["id"], content="old", embedding=[0.1] * 4, chunk_index=0
    )

    await _signup(client, ADMIN_EMAIL)

    fake_supadata = AsyncMock(
        return_value={
            "title": "New Title",
            "description": "New Desc",
            "transcript": "brand new transcript content",
            "youtube_video_id": "dQw4w9WgXcQ",
            "segments": [],
        }
    )

    # Stub the chunker + embedder so we don't need the real ONNX model.
    with (
        patch(
            "backend.routes.admin.fetch_video_for_ingest",
            new=fake_supadata,
        ),
        patch(
            "backend.routes.admin.chunk_video",
            return_value=["new-0", "new-1", "new-2"],
        ),
        patch(
            "backend.routes.admin.embed_batch",
            return_value=[[0.5] * 4, [0.6] * 4, [0.7] * 4],
        ),
    ):
        r = await client.post(f"/api/admin/videos/{video['id']}/re-sync")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chunks_created"] == 3

    chunks = await repository.list_chunks_for_video(video["id"])
    assert [c["content"] for c in chunks] == ["new-0", "new-1", "new-2"]


async def test_resync_unknown_video_returns_404(client):
    await _signup(client, ADMIN_EMAIL)
    r = await client.post("/api/admin/videos/nope/re-sync")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Add by URL
# ---------------------------------------------------------------------------


async def test_add_video_by_url_succeeds(client):
    from backend.db import repository

    fake_supadata = AsyncMock(
        return_value={
            "title": "New Video",
            "description": "New Desc",
            "transcript": "transcript text",
            "youtube_video_id": "abc12345678",
            "segments": [],
        }
    )

    await _signup(client, ADMIN_EMAIL)

    with (
        patch(
            "backend.routes.admin.fetch_video_for_ingest",
            new=fake_supadata,
        ),
        patch("backend.routes.admin.chunk_video", return_value=["c0", "c1"]),
        patch(
            "backend.routes.admin.embed_batch",
            return_value=[[0.1] * 4, [0.2] * 4],
        ),
    ):
        r = await client.post(
            "/api/admin/videos",
            json={"url": "https://www.youtube.com/watch?v=abc12345678"},
        )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["chunks_created"] == 2

    videos = await repository.list_videos()
    assert len(videos) == 1
    assert videos[0]["title"] == "New Video"


async def test_add_video_rejects_duplicate(client):
    from backend.db import repository

    # Seed a video that already carries this youtube id in its URL
    await repository.create_video(
        title="Existing",
        description="D",
        url="https://www.youtube.com/watch?v=abc12345678",
        transcript="t",
    )

    fake_supadata = AsyncMock(
        return_value={
            "title": "T",
            "description": "D",
            "transcript": "tx",
            "youtube_video_id": "abc12345678",
            "segments": [],
        }
    )

    await _signup(client, ADMIN_EMAIL)

    with (
        patch(
            "backend.routes.admin.fetch_video_for_ingest",
            new=fake_supadata,
        ),
        patch("backend.routes.admin.chunk_video", return_value=["c0"]),
        patch("backend.routes.admin.embed_batch", return_value=[[0.1] * 4]),
    ):
        r = await client.post(
            "/api/admin/videos",
            json={"url": "https://www.youtube.com/watch?v=abc12345678"},
        )

    assert r.status_code == 409


async def test_add_video_rejects_non_youtube_url(client):
    await _signup(client, ADMIN_EMAIL)
    r = await client.post(
        "/api/admin/videos",
        json={"url": "https://example.com/not-youtube"},
    )
    assert r.status_code == 400


async def test_add_video_non_admin_returns_403(client):
    await _signup(client, "regular@example.com")
    r = await client.post(
        "/api/admin/videos",
        json={"url": "https://www.youtube.com/watch?v=abc12345678"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# sync-channel delegation + config guard
# ---------------------------------------------------------------------------


async def test_sync_channel_delegates_to_channel_worker(client, monkeypatch):
    from backend.routes import admin as admin_route

    called = {"count": 0}

    async def fake_worker():
        called["count"] += 1
        return {
            "sync_run_id": "s1",
            "status": "completed",
            "videos_total": 0,
            "videos_new": 0,
            "videos_error": 0,
        }

    monkeypatch.setattr(admin_route, "_sync_channel_impl", fake_worker)

    await _signup(client, ADMIN_EMAIL)
    r = await client.post("/api/admin/videos/sync-channel")
    assert r.status_code == 200
    assert called["count"] == 1
    assert r.json()["status"] == "completed"


async def test_sync_channel_returns_400_when_unconfigured(client, monkeypatch):
    from backend.routes import admin as admin_route

    monkeypatch.setattr(admin_route, "YOUTUBE_CHANNEL_ID", "")
    monkeypatch.setattr(admin_route, "SUPADATA_API_KEY", "")

    await _signup(client, ADMIN_EMAIL)
    r = await client.post("/api/admin/videos/sync-channel")
    assert r.status_code == 400
