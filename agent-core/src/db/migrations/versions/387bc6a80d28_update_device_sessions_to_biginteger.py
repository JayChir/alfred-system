"""update_device_sessions_to_biginteger

Revision ID: 387bc6a80d28
Revises: d1900b56301e
Create Date: 2025-09-07 21:37:36.354951

Updates device_sessions token counters from Integer to BigInteger to handle
large token volumes in production environments. This prevents overflow issues
when tracking token usage over extended periods.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "387bc6a80d28"
down_revision: Union[str, Sequence[str], None] = "d1900b56301e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Alter device_sessions columns from Integer to BigInteger for better scalability.

    This migration updates:
    - tokens_input_total: Integer -> BigInteger
    - tokens_output_total: Integer -> BigInteger
    - request_count: Integer -> BigInteger

    BigInteger supports values up to 9,223,372,036,854,775,807 which is sufficient
    for tracking token usage even at high volumes.
    """
    # Update tokens_input_total from Integer to BigInteger
    op.alter_column(
        "device_sessions",
        "tokens_input_total",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
        existing_server_default="0",
        comment="Total input tokens consumed (BigInteger for scale)",
    )

    # Update tokens_output_total from Integer to BigInteger
    op.alter_column(
        "device_sessions",
        "tokens_output_total",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
        existing_server_default="0",
        comment="Total output tokens generated (BigInteger for scale)",
    )

    # Update request_count from Integer to BigInteger
    op.alter_column(
        "device_sessions",
        "request_count",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
        existing_server_default="0",
        comment="Number of requests made with this session (BigInteger for scale)",
    )


def downgrade() -> None:
    """
    Revert device_sessions columns from BigInteger back to Integer.

    WARNING: This may cause data loss if values exceed Integer range (2,147,483,647).
    """
    # Revert tokens_input_total from BigInteger to Integer
    op.alter_column(
        "device_sessions",
        "tokens_input_total",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
        existing_server_default="0",
        comment="Total input tokens consumed",
    )

    # Revert tokens_output_total from BigInteger to Integer
    op.alter_column(
        "device_sessions",
        "tokens_output_total",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
        existing_server_default="0",
        comment="Total output tokens generated",
    )

    # Revert request_count from BigInteger to Integer
    op.alter_column(
        "device_sessions",
        "request_count",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
        existing_server_default="0",
        comment="Number of requests made with this session",
    )
