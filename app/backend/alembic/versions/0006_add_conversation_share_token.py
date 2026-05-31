"""Add share_token and share_created_at to conversations.

Schema changes for issue #278 (shareable read-only conversation links):
- conversations: share_token (unique, nullable), share_created_at (nullable)

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-31

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("share_token", sa.Text(), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("share_created_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_conversations_share_token",
        "conversations",
        ["share_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_share_token", table_name="conversations")
    op.drop_column("conversations", "share_created_at")
    op.drop_column("conversations", "share_token")
