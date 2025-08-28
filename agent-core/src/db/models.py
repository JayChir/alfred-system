"""
Database models for Alfred Agent Core.

This module defines SQLAlchemy models for:
- User management and authentication
- Notion OAuth connections with encrypted token storage
- Session and cache management

Security: All OAuth tokens are encrypted at rest using Fernet encryption.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    ARRAY,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, ENUM, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


class User(Base):
    """
    User model for authentication and account management.

    Attributes:
        id: Unique user identifier (UUID)
        email: User's email address (case-insensitive via CITEXT)
        status: Account status (active, inactive, suspended)
        created_at: Account creation timestamp
        updated_at: Last modification timestamp
    """

    __tablename__ = "users"

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc="Unique user identifier",
    )

    # User information
    email: Mapped[str] = mapped_column(
        CITEXT,
        unique=True,
        nullable=False,
        doc="User's email address (case-insensitive)",
    )

    status: Mapped[str] = mapped_column(
        ENUM("active", "inactive", "suspended", name="user_status", create_type=False),
        nullable=False,
        default="active",
        doc="Account status: active, inactive, suspended",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        doc="Account creation timestamp",
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        doc="Last modification timestamp",
    )

    # Relationships
    notion_connections: Mapped[list["NotionConnection"]] = relationship(
        "NotionConnection",
        back_populates="user",
        cascade="all, delete-orphan",
        doc="All Notion OAuth connections for this user",
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email}, status={self.status})>"


class NotionConnection(Base):
    """
    Notion OAuth connection with encrypted token storage.

    Stores encrypted access/refresh tokens and connection metadata for Notion OAuth.
    Supports multiple workspaces per user and token refresh flows.

    Security: access_token and refresh_token are encrypted using Fernet encryption.
    The key_version field supports key rotation.

    Attributes:
        id: Unique connection identifier
        user_id: Foreign key to User
        provider: OAuth provider (always 'notion')
        workspace_id: Notion workspace ID from OAuth response
        bot_id: Notion bot ID (if present in token response)
        scopes: Array of granted OAuth scopes
        access_token_ciphertext: Encrypted access token
        refresh_token_ciphertext: Encrypted refresh token (if available)
        access_token_expires_at: Access token expiration
        refresh_token_expires_at: Refresh token expiration (nullable)
        key_version: Encryption key version for rotation
        revoked_at: Revocation timestamp (NULL = active)
    """

    __tablename__ = "notion_connections"

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc="Unique connection identifier",
    )

    # Foreign key to User
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        doc="Foreign key to User table",
    )

    # Provider information
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False, default="notion", doc="OAuth provider name"
    )

    workspace_id: Mapped[str] = mapped_column(
        String(255), nullable=False, doc="Notion workspace ID from OAuth response"
    )

    bot_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, doc="Notion bot ID (if present in token response)"
    )

    scopes: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(String), nullable=True, doc="Array of granted OAuth scopes"
    )

    # Encrypted token storage
    access_token_ciphertext: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False, doc="Encrypted access token (Fernet encrypted)"
    )

    refresh_token_ciphertext: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary,
        nullable=True,
        doc="Encrypted refresh token (Fernet encrypted, if available)",
    )

    # Token expiration tracking
    access_token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, doc="Access token expiration timestamp"
    )

    refresh_token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, doc="Refresh token expiration timestamp"
    )

    # Encryption key management
    key_version: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=1,
        doc="Encryption key version for rotation support",
    )

    # Connection lifecycle
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Connection revocation timestamp (NULL = active)",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        doc="Connection creation timestamp",
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        doc="Last modification timestamp",
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User", back_populates="notion_connections", doc="User who owns this connection"
    )

    # Indexes and constraints
    __table_args__ = (
        # Unique constraint to prevent duplicate connections
        # Using separate constraint for the COALESCE expression
        UniqueConstraint(
            "user_id",
            "workspace_id",
            "provider",
            "bot_id",
            name="uq_nc_user_ws_provider_bot",
        ),
        # Index on user_id for efficient user connection lookups
        Index("ix_nc_user_id", "user_id"),
        # Partial index for active connections (revoked_at IS NULL)
        # This optimizes queries for active connections
        Index(
            "ix_nc_active",
            "user_id",
            "workspace_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    @property
    def is_active(self) -> bool:
        """Check if this connection is currently active (not revoked)."""
        return self.revoked_at is None

    @property
    def is_access_token_expired(self) -> bool:
        """Check if the access token is expired."""
        if not self.access_token_expires_at:
            return False  # No expiration set, assume long-lived
        return datetime.now(timezone.utc) >= self.access_token_expires_at

    @property
    def is_refresh_token_expired(self) -> bool:
        """Check if the refresh token is expired."""
        if not self.refresh_token_expires_at:
            return False  # No expiration set, assume long-lived
        return datetime.now(timezone.utc) >= self.refresh_token_expires_at

    def revoke(self) -> None:
        """Mark this connection as revoked."""
        self.revoked_at = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        status = "active" if self.is_active else "revoked"
        return (
            f"<NotionConnection(id={self.id}, user_id={self.user_id}, "
            f"workspace_id={self.workspace_id}, status={status})>"
        )
