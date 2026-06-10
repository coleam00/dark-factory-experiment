"""Add a GIN index on messages.sources for the video filter (issue #294).

The conversation list can now be filtered by video. The filter runs a JSONB
containment subquery (`m.sources @> '[{"video_id": ...}]'`) over messages;
without an index that is a full-table scan. `jsonb_path_ops` is the optimal
operator class for `@>` containment-only lookups.

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
        "CREATE INDEX IF NOT EXISTS idx_messages_sources_gin "
        "ON messages USING gin (sources jsonb_path_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_messages_sources_gin")
