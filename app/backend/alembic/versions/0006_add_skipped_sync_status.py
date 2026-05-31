"""Add 'skipped' to channel_sync_videos status CHECK constraint.

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
    # PostgreSQL auto-generates the constraint name as
    # channel_sync_videos_status_check for an inline column CHECK.
    op.execute(
        "ALTER TABLE channel_sync_videos DROP CONSTRAINT IF EXISTS channel_sync_videos_status_check"
    )
    op.execute(
        "ALTER TABLE channel_sync_videos ADD CONSTRAINT channel_sync_videos_status_check "
        "CHECK (status IN ('pending', 'ingested', 'error', 'skipped'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE channel_sync_videos DROP CONSTRAINT IF EXISTS channel_sync_videos_status_check"
    )
    op.execute(
        "ALTER TABLE channel_sync_videos ADD CONSTRAINT channel_sync_videos_status_check "
        "CHECK (status IN ('pending', 'ingested', 'error'))"
    )
