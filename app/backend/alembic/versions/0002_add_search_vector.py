"""Add search_vector tsvector column to chunks.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-18

"""
from __future__ import annotations

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Add search_vector tsvector column (STORED GENERATED)
    op.execute(
        """
        ALTER TABLE chunks
        ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
    """
    )
    # GIN index for fast full-text search
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS chunks_search_vector_idx
        ON chunks USING GIN(search_vector)
    """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS chunks_search_vector_idx")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS search_vector")