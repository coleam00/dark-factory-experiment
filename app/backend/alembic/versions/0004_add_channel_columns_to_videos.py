"""Add channel_id and channel_title columns to videos.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-21

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE videos
        ADD COLUMN IF NOT EXISTS channel_id TEXT,
        ADD COLUMN IF NOT EXISTS channel_title TEXT
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE videos DROP COLUMN IF EXISTS channel_title")
    op.execute("ALTER TABLE videos DROP COLUMN IF EXISTS channel_id")
