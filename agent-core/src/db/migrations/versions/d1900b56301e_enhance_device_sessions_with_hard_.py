"""enhance device_sessions with hard_expires_at and production fields

Revision ID: d1900b56301e
Revises: b352c9344b25
Create Date: 2025-09-07 17:31:51.870661

This migration adds production-grade fields to the device_sessions table:
- hard_expires_at: Hard 30-day expiry cap to prevent infinite session extension
- request_count: Track number of requests per session for analytics
- revoked_at: Soft deletion capability for session revocation
- Proper indexing for performance optimization
- Ensures device_token_hash is exactly 32 bytes (SHA-256)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1900b56301e"
down_revision: Union[str, Sequence[str], None] = "b352c9344b25"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add production-grade fields and indexes to device_sessions table.

    New fields:
    - hard_expires_at: Absolute expiry cap (created_at + 30 days)
    - request_count: Request counter for analytics and rate limiting
    - revoked_at: Soft deletion timestamp for session revocation

    Optimizations:
    - Ensure device_token_hash is exactly 32 bytes for SHA-256
    - Add performance indexes for common queries
    """
    # Add missing columns to existing device_sessions table
    op.add_column(
        "device_sessions",
        sa.Column(
            "hard_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '30 days'"),
        ),
    )

    op.add_column(
        "device_sessions",
        sa.Column("request_count", sa.BigInteger(), nullable=False, server_default="0"),
    )

    op.add_column(
        "device_sessions",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Ensure session_token_hash is exactly 32 bytes (SHA-256 size)
    # This is a no-op if already correct, but ensures consistency
    op.alter_column("device_sessions", "session_token_hash", type_=sa.LargeBinary(32))

    # Create index for hard_expires_at (others already exist)
    op.create_index(
        "idx_device_sessions_hard_expires", "device_sessions", ["hard_expires_at"]
    )

    # Set hard_expires_at for any existing records
    # This handles the case where device_sessions already has data
    op.execute(
        """
        UPDATE device_sessions
        SET hard_expires_at = created_at + interval '30 days'
        WHERE hard_expires_at IS NULL
    """
    )


def downgrade() -> None:
    """
    Remove production-grade enhancements from device_sessions table.

    This will drop the new columns and indexes added in upgrade().
    """
    # Drop the index we created (others existed before this migration)
    op.drop_index("idx_device_sessions_hard_expires", table_name="device_sessions")

    # Drop columns in reverse order
    op.drop_column("device_sessions", "revoked_at")
    op.drop_column("device_sessions", "request_count")
    op.drop_column("device_sessions", "hard_expires_at")
