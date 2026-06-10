"""Add per-conversation video scope (issue #279).

Adds `scoped_video_ids TEXT[]` to `conversations`. NULL (the default for all
existing and new rows) means the conversation is unscoped and retrieval
searches the whole library — the pre-#279 behavior. A non-empty array
restricts retrieval and citations to those videos only.

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
    # Nullable, no default: NULL = unscoped. An empty array is never stored
    # (the API normalizes [] to NULL) to avoid the "scope to nothing" ambiguity.
    op.execute(
        """
        ALTER TABLE conversations
        ADD COLUMN IF NOT EXISTS scoped_video_ids TEXT[]
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS scoped_video_ids")
