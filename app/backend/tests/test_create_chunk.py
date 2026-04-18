"""
Tests for repository.create_chunk with new timestamp fields.

Verifies:
  - create_chunk stores and returns start_seconds, end_seconds, snippet correctly
  - Round-trip through list_chunks preserves the new fields

NOTE: Tests in this module were written against the SQLite schema with
`aiosqlite` + `schema.init_db`. After the Postgres/Alembic migration they
need a rewrite against a real test Postgres. Skipped pending that rewrite.
"""

import pytest

pytestmark = pytest.mark.skip(
    reason="Tests require SQLite schema.init_db; pending rewrite for asyncpg/Alembic."
)

try:
    import aiosqlite
except ImportError:
    aiosqlite = None

from backend.db import repository  # noqa: E402


class TestCreateChunkWithTimestamps:
    async def test_create_chunk_with_timestamp_fields(
        self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """create_chunk stores and returns start_seconds, end_seconds, snippet."""
        db_path = tmp_path / "test_chunk.db"  # type: ignore[operator]

        import backend.config

        monkeypatch.setattr(backend.config, "DB_PATH", str(db_path))
        monkeypatch.setattr(repository, "DB_PATH", str(db_path))
        monkeypatch.setattr(schema, "DB_PATH", str(db_path))  # type: ignore[name-defined]  # noqa: F821

        async with aiosqlite.connect(db_path):
            await schema.init_db()  # type: ignore[name-defined]  # noqa: F821

        # Create a video first (required for FK constraint)
        video = await repository.create_video(
            title="Test Video",
            description="A test video",
            url="https://youtube.com/watch?v=abc123",
            transcript="This is a test transcript",
        )
        video_id = video["id"]

        # Create a chunk with timestamp fields
        mock_embedding = [0.1] * 1536
        result = await repository.create_chunk(
            video_id=video_id,
            content="Test chunk content",
            embedding=mock_embedding,
            chunk_index=0,
            start_seconds=10.5,
            end_seconds=20.0,
            snippet="Test snippet text",
        )

        assert result["start_seconds"] == 10.5
        assert result["end_seconds"] == 20.0
        assert result["snippet"] == "Test snippet text"

    async def test_create_chunk_roundtrip_through_list_chunks(
        self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Chunks persist correctly through list_chunks."""
        db_path = tmp_path / "test_chunk_roundtrip.db"  # type: ignore[operator]

        import backend.config

        monkeypatch.setattr(backend.config, "DB_PATH", str(db_path))
        monkeypatch.setattr(repository, "DB_PATH", str(db_path))
        monkeypatch.setattr(schema, "DB_PATH", str(db_path))  # type: ignore[name-defined]  # noqa: F821

        async with aiosqlite.connect(db_path):
            await schema.init_db()  # type: ignore[name-defined]  # noqa: F821

        video = await repository.create_video(
            title="Test Video",
            description="A test video",
            url="https://youtube.com/watch?v=abc123",
            transcript="This is a test transcript",
        )
        video_id = video["id"]

        mock_embedding = [0.1] * 1536
        await repository.create_chunk(
            video_id=video_id,
            content="First chunk",
            embedding=mock_embedding,
            chunk_index=0,
            start_seconds=0.0,
            end_seconds=10.0,
            snippet="First snippet",
        )
        await repository.create_chunk(
            video_id=video_id,
            content="Second chunk",
            embedding=mock_embedding,
            chunk_index=1,
            start_seconds=10.0,
            end_seconds=20.0,
            snippet="Second snippet",
        )

        # Use list_chunks_for_video to avoid interference from other tests
        chunks = await repository.list_chunks_for_video(video_id)
        assert len(chunks) == 2

        # Verify timestamps are preserved
        first = next(c for c in chunks if c["content"] == "First chunk")
        assert first["start_seconds"] == 0.0
        assert first["end_seconds"] == 10.0
        assert first["snippet"] == "First snippet"

        second = next(c for c in chunks if c["content"] == "Second chunk")
        assert second["start_seconds"] == 10.0
        assert second["end_seconds"] == 20.0
        assert second["snippet"] == "Second snippet"
