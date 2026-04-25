"""Add multi-source-type support to videos/chunks + Circle membership flag on users.

Schema changes for issue #147 (gated Dynamous content):
- videos: source_type, content_hash, content_path, lesson_url, metadata
- chunks: source_type (denormalized for fast ACL filtering)
- users: is_member, member_verified_at

The `videos` table stays named `videos` (no rename) — the new columns let it hold
both YouTube and Dynamous course/workshop sources. New columns use safe defaults so
existing rows upgrade in-place.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-25

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # --- videos: multi-source-type support -------------------------------
    # source_type discriminates YouTube vs Dynamous content. Existing rows
    # default to 'youtube' which is correct since pre-#147 only YouTube was
    # ingested.
    op.execute(
        """
        ALTER TABLE videos
        ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'youtube',
        ADD COLUMN IF NOT EXISTS content_hash TEXT,
        ADD COLUMN IF NOT EXISTS content_path TEXT,
        ADD COLUMN IF NOT EXISTS lesson_url TEXT,
        ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS videos_content_path_idx "
        "ON videos (content_path)"
    )

    # --- chunks: denormalize source_type for fast ACL filtering ----------
    # Default 'youtube' is correct for existing rows (all pre-#147 chunks
    # were embedded from YouTube transcripts).
    op.execute(
        """
        ALTER TABLE chunks
        ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'youtube'
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS chunks_source_type_idx "
        "ON chunks (source_type)"
    )

    # --- users: Circle membership tracking -------------------------------
    op.execute(
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS is_member BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS member_verified_at TIMESTAMPTZ
        """
    )


def downgrade() -> None:
    # Reverse in dependency order: drop indexes before columns
    op.execute("DROP INDEX IF EXISTS chunks_source_type_idx")
    op.execute("DROP INDEX IF EXISTS videos_content_path_idx")

    op.execute(
        """
        ALTER TABLE users
        DROP COLUMN IF EXISTS member_verified_at,
        DROP COLUMN IF EXISTS is_member
        """
    )

    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS source_type")

    op.execute(
        """
        ALTER TABLE videos
        DROP COLUMN IF EXISTS metadata,
        DROP COLUMN IF EXISTS lesson_url,
        DROP COLUMN IF EXISTS content_path,
        DROP COLUMN IF EXISTS content_hash,
        DROP COLUMN IF EXISTS source_type
        """
    )
