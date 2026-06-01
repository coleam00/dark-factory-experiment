"""Add video_ids scope to conversations.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-01

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE conversations
        ADD COLUMN IF NOT EXISTS video_ids TEXT[]
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS conversations_video_ids_idx ON conversations USING GIN (video_ids)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS conversations_video_ids_idx")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS video_ids")
