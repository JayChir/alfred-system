"""add_threads_and_tool_logging

Revision ID: 4947c8bca735
Revises: b85f1c0aec2a
Create Date: 2025-09-04 08:53:13.109510

Implements thread support for cross-device conversation continuity.
Creates threads, thread_messages, and tool_call_log tables.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

# revision identifiers, used by Alembic.
revision: str = "4947c8bca735"
down_revision: Union[str, Sequence[str], None] = "b85f1c0aec2a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create threads, thread_messages, and tool_call_log tables."""

    # Create required PostgreSQL extensions
    # pgcrypto for gen_random_uuid() - used for UUID generation
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    # citext already created in earlier migration (538147b69810)

    # Create threads table
    # This is the main conversation container for cross-device continuity
    op.create_table(
        "threads",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "owner_user_id",
            pg.UUID(as_uuid=True),
            nullable=True,
            # No server default - set from env var in code for MVP
        ),
        sa.Column("workspace_id", sa.Text(), nullable=True),
        sa.Column(
            "share_token_hash",
            sa.LargeBinary(),
            nullable=True,
            unique=True,
        ),
        sa.Column(
            "share_token_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        # Soft delete support for thread archival
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Create thread_messages table
    # Stores individual messages within a thread
    op.create_table(
        "thread_messages",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "thread_id",
            pg.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "content",
            pg.JSONB(),
            nullable=False,
        ),
        # For idempotency - client-provided ID prevents duplicates
        sa.Column(
            "client_message_id",
            sa.Text(),
            nullable=True,
        ),
        # For conversation threading/replies
        sa.Column(
            "in_reply_to",
            pg.UUID(as_uuid=True),
            nullable=True,
        ),
        # Status tracking for streaming and error states
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'complete'"),
        ),
        # Tool calls made during this message
        sa.Column(
            "tool_calls",
            pg.JSONB(),
            nullable=True,
        ),
        # Token tracking for billing and limits
        sa.Column("tokens_input", sa.Integer(), nullable=True),
        sa.Column("tokens_output", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["in_reply_to"], ["thread_messages.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'tool', 'system')",
            name="chk_message_role",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'streaming', 'complete', 'error')",
            name="chk_message_status",
        ),
    )

    # Create tool_call_log table for partial failure handling
    # Tracks tool executions even if LLM fails afterward
    op.create_table(
        "tool_call_log",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "request_id",
            pg.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "thread_id",
            pg.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            pg.UUID(as_uuid=True),
            nullable=True,  # May be NULL if LLM fails before creating assistant message
        ),
        sa.Column(
            "call_index",
            sa.Integer(),
            nullable=False,
        ),
        # For idempotency - prevents duplicate tool executions
        sa.Column(
            "idempotency_key",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "tool_name",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "args",
            pg.JSONB(),
            nullable=False,
        ),
        # Store result digest for cache invalidation
        sa.Column(
            "result_digest",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "error",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        # Use SET NULL instead of CASCADE so we keep tool logs even if message is deleted
        sa.ForeignKeyConstraint(
            ["message_id"], ["thread_messages.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'success', 'failed')",
            name="chk_tool_status",
        ),
    )

    # Create indexes for threads table
    op.create_index("idx_threads_owner", "threads", ["owner_user_id"])
    op.create_index("idx_threads_workspace", "threads", ["workspace_id"])
    op.create_index("idx_threads_activity", "threads", ["last_activity_at"])
    op.create_index("idx_threads_token_expires", "threads", ["share_token_expires_at"])

    # Create indexes for thread_messages table
    # Optimized composite index for hot path (tail N messages by time)
    op.create_index(
        "idx_msgs_thread_created", "thread_messages", ["thread_id", "created_at"]
    )
    op.create_index("idx_messages_status", "thread_messages", ["status"])

    # Unique constraint for client idempotency (per thread)
    # Uses partial index to handle NULL client_message_id
    op.create_index(
        "uq_thread_client_msg",
        "thread_messages",
        ["thread_id", "client_message_id"],
        unique=True,
        postgresql_where=sa.text("client_message_id IS NOT NULL"),
    )

    # Create indexes for tool_call_log table
    op.create_index("idx_tool_log_request", "tool_call_log", ["request_id"])
    op.create_index("idx_tool_log_thread", "tool_call_log", ["thread_id"])
    op.create_index(
        "idx_tool_log_idempotency", "tool_call_log", ["idempotency_key"], unique=True
    )


def downgrade() -> None:
    """Drop thread-related tables and indexes."""
    # Drop tool_call_log indexes and table
    op.drop_index("idx_tool_log_idempotency", table_name="tool_call_log")
    op.drop_index("idx_tool_log_thread", table_name="tool_call_log")
    op.drop_index("idx_tool_log_request", table_name="tool_call_log")
    op.drop_table("tool_call_log")

    # Drop thread_messages indexes and table
    op.drop_index("uq_thread_client_msg", table_name="thread_messages")
    op.drop_index("idx_messages_status", table_name="thread_messages")
    op.drop_index("idx_msgs_thread_created", table_name="thread_messages")
    op.drop_table("thread_messages")

    # Drop threads indexes and table
    op.drop_index("idx_threads_token_expires", table_name="threads")
    op.drop_index("idx_threads_activity", table_name="threads")
    op.drop_index("idx_threads_workspace", table_name="threads")
    op.drop_index("idx_threads_owner", table_name="threads")
    op.drop_table("threads")
