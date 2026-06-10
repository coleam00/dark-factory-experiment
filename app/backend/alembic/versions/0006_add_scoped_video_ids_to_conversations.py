"""Add per-conversation video scope (issue #279).

Adds a nullable ``scoped_video_ids TEXT[]`` column to ``conversations``. When a
conversation is created with a non-empty list, every retrieval path for that
conversation's messages filters chunks to those ``video_id``s. NULL/empty means
"search the whole library" (the pre-#279 default), so existing rows upgrade
in-place with no behavior change.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-31

"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("scoped_video_ids", postgresql.ARRAY(sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "scoped_video_ids")
