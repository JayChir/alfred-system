"""add_cache_tags_and_improvements

Revision ID: 22f5e329390a
Revises: 387bc6a80d28
Create Date: 2025-09-07 22:09:14.525933

Adds cache_tags table for tag-based invalidation and updates agent_cache table
with additional fields for better cache management including size tracking,
partial indexes, and improved constraints.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "22f5e329390a"
down_revision: Union[str, Sequence[str], None] = "387bc6a80d28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add cache_tags table and update agent_cache with improvements.

    This migration:
    1. Adds size_bytes column to agent_cache for payload size tracking
    2. Creates agent_cache_tags table for tag-based invalidation
    3. Adds partial index for active cache entries
    4. Adds size constraint to ensure positive values
    """

    # Add size_bytes column to agent_cache if it doesn't exist
    op.add_column(
        "agent_cache",
        sa.Column(
            "size_bytes",
            sa.Integer(),
            nullable=True,
            comment="Size of cached content in bytes",
        ),
    )

    # Create agent_cache_tags table for tag-based invalidation
    op.create_table(
        "agent_cache_tags",
        sa.Column("cache_key", sa.Text(), nullable=False),
        sa.Column("tag", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["cache_key"], ["agent_cache.cache_key"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("cache_key", "tag"),
        comment="Tag index for cache invalidation",
    )

    # Create index on tag column for efficient tag-based queries
    op.create_index("idx_agent_cache_tags_tag", "agent_cache_tags", ["tag"])

    # Add partial index for active cache entries (PostgreSQL-specific)
    # This index helps with cleanup queries and active entry lookups
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_cache_active
        ON agent_cache (expires_at)
        WHERE expires_at > NOW()
    """
    )

    # Add check constraint for size_bytes if provided
    op.create_check_constraint(
        "chk_cache_size_positive", "agent_cache", "size_bytes IS NULL OR size_bytes > 0"
    )

    # Update any existing cache entries to calculate size_bytes from content
    # This is a one-time migration to populate the new column
    op.execute(
        """
        UPDATE agent_cache
        SET size_bytes = octet_length(content::text)
        WHERE size_bytes IS NULL AND content IS NOT NULL
    """
    )


def downgrade() -> None:
    """
    Remove cache_tags table and revert agent_cache improvements.
    """

    # Drop the size constraint
    op.drop_constraint("chk_cache_size_positive", "agent_cache", type_="check")

    # Drop the partial index for active entries
    op.execute("DROP INDEX IF EXISTS idx_agent_cache_active")

    # Drop the cache_tags table and its index
    op.drop_index("idx_agent_cache_tags_tag", table_name="agent_cache_tags")
    op.drop_table("agent_cache_tags")

    # Drop the size_bytes column
    op.drop_column("agent_cache", "size_bytes")
