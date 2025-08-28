"""Production hardening: fix NULL bot_id uniqueness, add DB defaults, triggers, and constraints

This migration implements several production-grade improvements:
1. Fix NULL bot_id uniqueness issue with COALESCE-based unique index
2. Enable pgcrypto extension for gen_random_uuid()
3. Add database-side defaults for UUIDs and timestamps
4. Create triggers for reliable updated_at maintenance
5. Add CHECK constraints for data validation
6. Create ENUM type for user status

Revision ID: 564c141b5690
Revises: 538147b69810
Create Date: 2025-08-28 11:30:15.584265

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "564c141b5690"
down_revision: Union[str, Sequence[str], None] = "538147b69810"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Apply production hardening improvements."""

    # 1. Enable pgcrypto extension for gen_random_uuid()
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # 2. Create user_status ENUM type
    user_status_enum = postgresql.ENUM(
        "active", "inactive", "suspended", name="user_status", create_type=True
    )
    user_status_enum.create(op.get_bind(), checkfirst=True)

    # 3. Drop existing problematic unique constraint
    op.drop_constraint(
        "uq_nc_user_ws_provider_bot", "notion_connections", type_="unique"
    )

    # 4. Create proper unique index handling NULL bot_id values
    # This ensures uniqueness even when bot_id is NULL
    op.execute(
        """
        CREATE UNIQUE INDEX uq_nc_user_ws_provider_bot_coalesced
        ON notion_connections (user_id, workspace_id, provider, COALESCE(bot_id, ''))
    """
    )

    # 5. Add database-side defaults for UUIDs and timestamps
    # Users table
    op.alter_column("users", "id", server_default=sa.text("gen_random_uuid()"))
    op.alter_column("users", "created_at", server_default=sa.text("now()"))
    op.alter_column("users", "updated_at", server_default=sa.text("now()"))

    # Notion connections table
    op.alter_column(
        "notion_connections", "id", server_default=sa.text("gen_random_uuid()")
    )
    op.alter_column("notion_connections", "created_at", server_default=sa.text("now()"))
    op.alter_column("notion_connections", "updated_at", server_default=sa.text("now()"))

    # 6. Create function and triggers for reliable updated_at maintenance
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """
    )

    op.execute(
        """
        CREATE TRIGGER trg_users_updated_at
        BEFORE UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """
    )

    op.execute(
        """
        CREATE TRIGGER trg_nc_updated_at
        BEFORE UPDATE ON notion_connections
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """
    )

    # 7. Add CHECK constraints for data validation
    # Email cannot be empty
    op.create_check_constraint(
        "ck_users_email_not_empty", "users", sa.text("email <> ''")
    )

    # Workspace ID cannot be empty
    op.create_check_constraint(
        "ck_nc_workspace_id_not_empty",
        "notion_connections",
        sa.text("workspace_id <> ''"),
    )

    # Provider cannot be empty
    op.create_check_constraint(
        "ck_nc_provider_not_empty", "notion_connections", sa.text("provider <> ''")
    )

    # Key version must be positive
    op.create_check_constraint(
        "ck_nc_key_version_positive", "notion_connections", sa.text("key_version > 0")
    )

    # 8. Convert user status to ENUM (data migration + column type change)
    # First, ensure all existing status values are valid
    op.execute(
        """
        UPDATE users SET status = 'active'
        WHERE status NOT IN ('active', 'inactive', 'suspended')
    """
    )

    # Change column type to ENUM
    op.alter_column(
        "users",
        "status",
        type_=user_status_enum,
        postgresql_using="status::user_status",
    )

    # 9. Add workspace_id index for performance
    op.create_index("ix_nc_workspace_id", "notion_connections", ["workspace_id"])


def downgrade() -> None:
    """Rollback production hardening changes."""

    # Remove indexes and constraints (reverse order)
    op.drop_index("ix_nc_workspace_id", "notion_connections")

    # Revert user status to varchar
    op.alter_column(
        "users", "status", type_=sa.String(20), postgresql_using="status::varchar"
    )

    # Drop ENUM type
    user_status_enum = postgresql.ENUM(name="user_status")
    user_status_enum.drop(op.get_bind(), checkfirst=True)

    # Drop CHECK constraints
    op.drop_constraint("ck_nc_key_version_positive", "notion_connections")
    op.drop_constraint("ck_nc_provider_not_empty", "notion_connections")
    op.drop_constraint("ck_nc_workspace_id_not_empty", "notion_connections")
    op.drop_constraint("ck_users_email_not_empty", "users")

    # Drop triggers and function
    op.execute("DROP TRIGGER IF EXISTS trg_nc_updated_at ON notion_connections")
    op.execute("DROP TRIGGER IF EXISTS trg_users_updated_at ON users")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")

    # Remove database defaults
    op.alter_column("notion_connections", "updated_at", server_default=None)
    op.alter_column("notion_connections", "created_at", server_default=None)
    op.alter_column("notion_connections", "id", server_default=None)
    op.alter_column("users", "updated_at", server_default=None)
    op.alter_column("users", "created_at", server_default=None)
    op.alter_column("users", "id", server_default=None)

    # Drop the COALESCE-based unique index
    op.execute("DROP INDEX IF EXISTS uq_nc_user_ws_provider_bot_coalesced")

    # Recreate the original problematic constraint
    op.create_unique_constraint(
        "uq_nc_user_ws_provider_bot",
        "notion_connections",
        ["user_id", "workspace_id", "provider", "bot_id"],
    )
