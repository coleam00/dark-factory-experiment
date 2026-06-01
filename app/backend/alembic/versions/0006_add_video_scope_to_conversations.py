"""Add per-conversation video scope (issue #279).

Lets a user restrict a conversation to a subset of videos so the assistant's
answers and citations only draw from those videos. The scope persists for the
life of the conversation.

`video_scope` is a nullable TEXT[] of video ids:
  - NULL (default) → unscoped; retrieval searches the whole library (today's
    behaviour, unchanged for every existing conversation).
  - non-empty array → retrieval is restricted to those video ids.

TEXT[] (not JSONB) so the scope reads back as a native Python list with no
codec dance and slots straight into the existing `video_id = ANY($n::text[])`
SQL filter pattern used by keyword/vector search.

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
    # Nullable, no default — NULL means "unscoped" so existing conversations
    # keep searching the whole library.
    op.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS video_scope TEXT[]")


def downgrade() -> None:
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS video_scope")
