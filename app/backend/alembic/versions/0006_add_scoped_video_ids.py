"""Add scoped_video_ids column to conversations (issue #279).

Lets a conversation be restricted to a user-selected subset of videos. When
`scoped_video_ids` is non-null, retrieval (keyword + vector) only sees chunks
whose `video_id` is in the list; NULL preserves the existing "search
everything" behavior. Stored as TEXT[] because video ids are plain text UUIDs.

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
    # Nullable with no default — NULL means "no scope set" (search everything),
    # which is the correct in-place upgrade for every existing conversation.
    op.execute(
        """
        ALTER TABLE conversations
        ADD COLUMN IF NOT EXISTS scoped_video_ids TEXT[]
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS scoped_video_ids")
