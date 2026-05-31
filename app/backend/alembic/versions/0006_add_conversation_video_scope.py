"""Add scoped_video_ids to conversations.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-31

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
        ADD COLUMN IF NOT EXISTS scoped_video_ids TEXT[]
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE conversations
        DROP COLUMN IF EXISTS scoped_video_ids
        """
    )
