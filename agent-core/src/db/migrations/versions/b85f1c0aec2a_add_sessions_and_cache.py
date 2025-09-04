"""add_sessions_and_cache

Revision ID: b85f1c0aec2a
Revises: c3d4e5f6a7b8
Create Date: 2025-09-03 22:08:28.779232

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

# revision identifiers, used by Alembic.
revision: str = "b85f1c0aec2a"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create user_sessions and agent_cache tables with production-ready schema."""
    # Create required PostgreSQL extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")  # for gen_random_uuid()
    # Note: citext extension already created in earlier migration (538147b69810)

    # Create user_sessions table
    op.create_table(
        "user_sessions",
        sa.Column(
            "session_id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=True),
        sa.Column("session_token_hash", sa.LargeBinary(), nullable=False, unique=True),
        sa.Column(
            "context_data",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "tokens_input_total", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "tokens_output_total", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_accessed",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    # Create indexes for user_sessions
    op.create_index("idx_user_sessions_user", "user_sessions", ["user_id"])
    op.create_index(
        "idx_user_sessions_last_accessed", "user_sessions", ["last_accessed"]
    )
    op.create_index("idx_user_sessions_expires_at", "user_sessions", ["expires_at"])

    # Add data validation constraints for user_sessions
    op.create_check_constraint(
        "chk_session_hash_len", "user_sessions", "octet_length(session_token_hash) = 32"
    )
    op.create_check_constraint(
        "chk_session_exp_future", "user_sessions", "expires_at > created_at"
    )

    # Create agent_cache table
    op.create_table(
        "agent_cache",
        sa.Column("cache_key", sa.Text(), primary_key=True),
        sa.Column("content", pg.JSONB(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column(
            "idempotent", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
    )

    # Create indexes for agent_cache
    op.create_index("idx_agent_cache_expires", "agent_cache", ["expires_at"])
    # Note: Partial index for cleanup would require IMMUTABLE function in WHERE clause
    # For now, use the general expires_at index for cleanup queries

    # Add data validation constraints for agent_cache
    op.create_check_constraint(
        "chk_cache_exp_future", "agent_cache", "expires_at > created_at"
    )


def downgrade() -> None:
    """Drop user_sessions and agent_cache tables and their constraints."""
    # Drop agent_cache table and related objects
    op.drop_constraint("chk_cache_exp_future", "agent_cache", type_="check")
    op.drop_index("idx_agent_cache_expires", table_name="agent_cache")
    op.drop_table("agent_cache")

    # Drop user_sessions table and related objects
    op.drop_constraint("chk_session_exp_future", "user_sessions", type_="check")
    op.drop_constraint("chk_session_hash_len", "user_sessions", type_="check")
    op.drop_index("idx_user_sessions_expires_at", table_name="user_sessions")
    op.drop_index("idx_user_sessions_last_accessed", table_name="user_sessions")
    op.drop_index("idx_user_sessions_user", table_name="user_sessions")
    op.drop_table("user_sessions")
