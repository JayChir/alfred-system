"""add_optional_thread_metadata

Revision ID: 9207948528a4
Revises: 4947c8bca735
Create Date: 2025-09-04 11:16:33.750153

Adds optional metadata fields for better thread organization and debugging:
- threads.title: Human-readable thread title
- threads.metadata: Flexible JSONB metadata storage
- thread_messages.request_id: Request ID for tracing and debugging
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

# revision identifiers, used by Alembic.
revision: str = "9207948528a4"
down_revision: Union[str, Sequence[str], None] = "4947c8bca735"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add optional metadata fields to threads and thread_messages tables."""

    # Add title and thread_metadata columns to threads table
    op.add_column("threads", sa.Column("title", sa.Text(), nullable=True))
    op.add_column("threads", sa.Column("thread_metadata", pg.JSONB(), nullable=True))

    # Add request_id column to thread_messages table for debugging
    op.add_column(
        "thread_messages", sa.Column("request_id", pg.UUID(as_uuid=True), nullable=True)
    )

    # Add index on request_id for efficient request tracing
    op.create_index(
        "idx_messages_request_id",
        "thread_messages",
        ["request_id"],
        postgresql_where=sa.text("request_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove optional metadata fields from threads and thread_messages tables."""

    # Drop the request_id index
    op.drop_index("idx_messages_request_id", table_name="thread_messages")

    # Remove columns from thread_messages
    op.drop_column("thread_messages", "request_id")

    # Remove columns from threads
    op.drop_column("threads", "thread_metadata")
    op.drop_column("threads", "title")
