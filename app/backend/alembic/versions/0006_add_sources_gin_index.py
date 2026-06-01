"""Add GIN index on messages.sources for fast video filtering.

Supports the conversation search by video feature (issue #294).
A GIN index lets `@> jsonb_build_array(jsonb_build_object('video_id', ...))`
run efficiently even with large message tables.

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
        "CREATE INDEX IF NOT EXISTS messages_sources_gin_idx ON messages USING GIN (sources)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS messages_sources_gin_idx")
