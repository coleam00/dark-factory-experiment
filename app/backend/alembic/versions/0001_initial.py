"""Initial schema — all tables migrated from SQLite to Postgres.

Revision ID: 0001
Revises:
Create Date: 2026-04-18

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # --- Extensions -------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # --- users -----------------------------------------------------------
    # UUIDs via gen_random_uuid() for new Postgres-native tables.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email CITEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_login_at TIMESTAMPTZ,
            daily_message_count INTEGER NOT NULL DEFAULT 0,
            rate_window_start TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS users_email_idx ON users (email)")

    # --- user_messages (sliding-window audit for 25 msg/user/24h cap) ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_messages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS user_messages_user_id_created_at_idx "
        "ON user_messages (user_id, created_at DESC)"
    )

    # --- signup_attempts (audit trail for signup rate-limiting) ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS signup_attempts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ip INET NOT NULL,
            email_attempted CITEXT,
            outcome TEXT NOT NULL CHECK (outcome IN (
                'accepted','ip_limited','global_limited','duplicate','invalid'
            )),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS signup_attempts_ip_created_at_idx "
        "ON signup_attempts (ip, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS signup_attempts_created_at_idx "
        "ON signup_attempts (created_at DESC)"
    )

    # --- videos ----------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS videos (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            url TEXT NOT NULL,
            transcript TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # --- chunks ---------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            embedding TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            start_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
            end_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
            snippet TEXT NOT NULL DEFAULT ''
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS chunks_video_id_idx ON chunks (video_id)")

    # --- conversations ---------------------------------------------------
    # user_id is TEXT (not UUID FK) to stay compatible with the auth service
    # which uses string user IDs. No FK constraint here.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT 'New Conversation',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS conversations_user_id_idx ON conversations (user_id)")

    # --- messages --------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # --- channel_sync_runs -----------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS channel_sync_runs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
            videos_total INTEGER NOT NULL DEFAULT 0,
            videos_new INTEGER NOT NULL DEFAULT 0,
            videos_error INTEGER NOT NULL DEFAULT 0,
            started_at TIMESTAMPTZ NOT NULL,
            finished_at TIMESTAMPTZ
        )
        """
    )

    # --- channel_sync_videos --------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS channel_sync_videos (
            id TEXT PRIMARY KEY,
            sync_run_id TEXT NOT NULL REFERENCES channel_sync_runs(id) ON DELETE CASCADE,
            youtube_video_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'ingested', 'error', 'skipped')),
            error_message TEXT,
            created_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS channel_sync_videos_sync_run_id_idx "
        "ON channel_sync_videos (sync_run_id)"
    )


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.execute("DROP TABLE IF EXISTS channel_sync_videos")
    op.execute("DROP TABLE IF EXISTS channel_sync_runs")
    op.execute("DROP TABLE IF EXISTS messages")
    op.execute("DROP TABLE IF EXISTS conversations")
    op.execute("DROP TABLE IF EXISTS chunks")
    op.execute("DROP TABLE IF EXISTS videos")
    op.execute("DROP TABLE IF EXISTS signup_attempts")
    op.execute("DROP TABLE IF EXISTS user_messages")
    op.execute("DROP TABLE IF EXISTS users")

    # Extensions are not dropped — they may be shared with other DB objects
    # or used by future migrations and leaving them avoids permission errors.
