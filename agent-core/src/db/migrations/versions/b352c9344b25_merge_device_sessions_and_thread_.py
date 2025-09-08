"""merge device_sessions and thread_metadata migrations

Revision ID: b352c9344b25
Revises: 04eec890c4bc, 9207948528a4
Create Date: 2025-09-07 17:31:44.144970

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "b352c9344b25"
down_revision: Union[str, Sequence[str], None] = ("04eec890c4bc", "9207948528a4")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
