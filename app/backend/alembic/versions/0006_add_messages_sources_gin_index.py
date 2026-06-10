"""Add GIN index on messages.sources for fast video-filter containment checks.

Issue #294 adds a "filter conversations by video" feature. The video link lives
inside `messages.sources` JSONB as `[{"video_id": "...", ...}]`, and filtering
uses a `sources @> '[{"video_id":"X"}]'::jsonb` containment check. Without a GIN
index that check is a sequential scan over messages; the index keeps it fast.

NOTE: plain `CREATE INDEX` (not CONCURRENTLY) — Alembic wraps each migration in a
transaction and `CREATE INDEX CONCURRENTLY` cannot run inside one. Plain index
creation is fine at current table sizes.

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
    op.execute("CREATE INDEX IF NOT EXISTS ix_messages_sources_gin ON messages USING GIN (sources)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_sources_gin")
