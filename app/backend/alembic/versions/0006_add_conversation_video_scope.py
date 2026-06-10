"""Add video_scope column to conversations.

Schema change for issue #279 (scope conversation to specific videos):
- conversations: video_scope TEXT[] (nullable, no default)

NULL means unscoped (whole-library behavior, backwards-compatible).
A non-empty array pins the conversation to those video ids.

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
    op.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS video_scope TEXT[]")


def downgrade() -> None:
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS video_scope")
