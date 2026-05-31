"""Add video_ids JSONB column to conversations (per-conversation video scope).

Stores an optional list of video_ids a conversation is scoped to (issue #279).
NULL means "unscoped" — search the whole library (today's behaviour). A
non-empty JSON array restricts retrieval and citations to those videos for the
conversation's lifetime. Additive and nullable, so existing rows upgrade
in-place with NULL (unscoped).

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-31

"""

from __future__ import annotations

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS video_ids JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS video_ids")
