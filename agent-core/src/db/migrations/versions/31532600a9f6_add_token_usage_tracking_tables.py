"""add token usage tracking tables

Revision ID: 31532600a9f6
Revises: 22f5e329390a
Create Date: 2025-09-09 18:31:25.620621

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "31532600a9f6"
down_revision: Union[str, None] = "22f5e329390a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Create token usage tracking tables for metering and budget management.

    Implements:
    - token_usage: Append-only log of all token consumption
    - token_usage_rollup_daily: Pre-aggregated daily usage for O(1) reads
    - user_token_budgets: Per-user/workspace budget configuration
    """

    # Create token_usage table (append-only log)
    op.create_table(
        "token_usage",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.String(255), nullable=True),
        sa.Column("device_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("provider", sa.String(50), nullable=True),
        sa.Column("tool_calls_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cache_hit", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("status", sa.String(10), nullable=False, server_default="ok"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "request_id", name="uq_token_usage_request_id"
        ),  # Idempotency
        sa.CheckConstraint(
            "status IN ('ok', 'error', 'cache')", name="ck_token_usage_status"
        ),
    )

    # Create indexes for efficient queries
    op.create_index(
        "idx_token_usage_user_workspace",
        "token_usage",
        ["user_id", "workspace_id", "created_at"],
    )
    op.create_index(
        "idx_token_usage_device_session",
        "token_usage",
        ["device_session_id", "created_at"],
    )
    op.create_index(
        "idx_token_usage_thread", "token_usage", ["thread_id", "created_at"]
    )
    op.create_index("idx_token_usage_created", "token_usage", ["created_at"])

    # Create token_usage_rollup_daily table (pre-aggregated for fast reads)
    op.create_table(
        "token_usage_rollup_daily",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("request_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cache_hits", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "workspace_id", "day", name="pk_token_rollup_daily"
        ),
    )

    # Index for efficient rollup queries
    op.create_index("idx_token_rollup_day", "token_usage_rollup_daily", ["day"])
    op.create_index(
        "idx_token_rollup_user", "token_usage_rollup_daily", ["user_id", "day"]
    )

    # Create user_token_budgets table
    op.create_table(
        "user_token_budgets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.String(255), nullable=True),
        sa.Column(
            "daily_limit", sa.Integer(), nullable=False, server_default="1000000"
        ),
        sa.Column(
            "monthly_limit", sa.Integer(), nullable=False, server_default="30000000"
        ),
        sa.Column(
            "warning_threshold_percent",
            sa.Integer(),
            nullable=False,
            server_default="80",
        ),
        sa.Column(
            "soft_block", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "workspace_id", name="uq_user_workspace_budget"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    # Add foreign key constraints for token_usage
    op.create_foreign_key(
        "fk_token_usage_user",
        "token_usage",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_token_usage_device_session",
        "token_usage",
        "device_sessions",
        ["device_session_id"],
        ["session_id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_token_usage_thread",
        "token_usage",
        "threads",
        ["thread_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Add foreign key for rollup table
    op.create_foreign_key(
        "fk_token_rollup_user",
        "token_usage_rollup_daily",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Drop token usage tracking tables."""

    # Drop foreign keys first
    op.drop_constraint(
        "fk_token_rollup_user", "token_usage_rollup_daily", type_="foreignkey"
    )
    op.drop_constraint("fk_token_usage_thread", "token_usage", type_="foreignkey")
    op.drop_constraint(
        "fk_token_usage_device_session", "token_usage", type_="foreignkey"
    )
    op.drop_constraint("fk_token_usage_user", "token_usage", type_="foreignkey")

    # Drop tables
    op.drop_table("user_token_budgets")
    op.drop_table("token_usage_rollup_daily")
    op.drop_table("token_usage")
