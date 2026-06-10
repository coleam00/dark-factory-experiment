"""Add scoped_video_ids to conversations for per-conversation video scoping.

Schema change for issue #279 (scope a conversation to specific videos):
- conversations: scoped_video_ids TEXT[] — NULL means "unscoped, search all
  videos" (the pre-#279 behaviour); a non-empty array is a hard retrieval
  filter for the life of the conversation. No default and no index: the
  column is only ever read per-conversation by primary key.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-10

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
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS scoped_video_ids")
