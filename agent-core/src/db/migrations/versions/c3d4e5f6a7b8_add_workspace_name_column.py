"""Add workspace_name column

Revision ID: c3d4e5f6a7b8
Revises: b28bb837a674
Create Date: 2025-08-29 05:45:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b28bb837a674"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add workspace_name column to notion_connections table."""
    op.add_column(
        "notion_connections",
        sa.Column("workspace_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    """Remove workspace_name column from notion_connections table."""
    op.drop_column("notion_connections", "workspace_name")
