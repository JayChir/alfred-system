"""rename session to device and clarify naming

Revision ID: 04eec890c4bc
Revises: b85f1c0aec2a
Create Date: 2025-09-04 15:26:54.018046

This migration renames session-related tables and columns to clarify naming:
- user_sessions → device_sessions (for device tokens and metering)
- oauth_states.session_id → oauth_states.flow_session_id (for OAuth CSRF)

This eliminates the ambiguous "session" term and gives each concept a clear name.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "04eec890c4bc"
down_revision: Union[str, Sequence[str], None] = "b85f1c0aec2a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Rename tables and columns for clarity:
    - user_sessions table → device_sessions (if it exists)
    - oauth_states.session_id → oauth_states.flow_session_id
    """
    # 1. Rename user_sessions table to device_sessions if it exists
    # Using IF EXISTS since the table might not be created yet in MVP
    op.execute("ALTER TABLE IF EXISTS user_sessions RENAME TO device_sessions")

    # 2. Rename primary key constraint if it exists
    op.execute(
        "ALTER INDEX IF EXISTS user_sessions_pkey RENAME TO device_sessions_pkey"
    )

    # 3. Rename any indexes that reference the old table name
    op.execute(
        "ALTER INDEX IF EXISTS ix_user_sessions_session_token RENAME TO ix_device_sessions_device_token_hash"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_user_sessions_user_id RENAME TO ix_device_sessions_user_id"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_user_sessions_expires_at RENAME TO ix_device_sessions_expires_at"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_user_sessions_last_accessed RENAME TO ix_device_sessions_last_accessed"
    )

    # 4. Rename column in oauth_states table
    # Check if the table and column exist before renaming
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'oauth_states'
                AND column_name = 'session_id'
            ) THEN
                ALTER TABLE oauth_states
                RENAME COLUMN session_id TO flow_session_id;
            END IF;
        END $$;
    """
    )

    # 5. If device_sessions table was renamed, also rename the session_token column
    # to device_token_hash to be clearer about what it stores
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'device_sessions'
                AND column_name = 'session_token'
            ) THEN
                ALTER TABLE device_sessions
                RENAME COLUMN session_token TO device_token_hash;
            END IF;
        END $$;
    """
    )


def downgrade() -> None:
    """
    Reverse the renames to restore original naming.
    """
    # Reverse the column renames
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'device_sessions'
                AND column_name = 'device_token_hash'
            ) THEN
                ALTER TABLE device_sessions
                RENAME COLUMN device_token_hash TO session_token;
            END IF;
        END $$;
    """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'oauth_states'
                AND column_name = 'flow_session_id'
            ) THEN
                ALTER TABLE oauth_states
                RENAME COLUMN flow_session_id TO session_id;
            END IF;
        END $$;
    """
    )

    # Reverse index renames
    op.execute(
        "ALTER INDEX IF EXISTS ix_device_sessions_last_accessed RENAME TO ix_user_sessions_last_accessed"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_device_sessions_expires_at RENAME TO ix_user_sessions_expires_at"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_device_sessions_user_id RENAME TO ix_user_sessions_user_id"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_device_sessions_device_token_hash RENAME TO ix_user_sessions_session_token"
    )

    # Reverse primary key rename
    op.execute(
        "ALTER INDEX IF EXISTS device_sessions_pkey RENAME TO user_sessions_pkey"
    )

    # Reverse table rename
    op.execute("ALTER TABLE IF EXISTS device_sessions RENAME TO user_sessions")
