"""
Database models for Alfred Agent Core.

This module defines SQLAlchemy models for:
- User management and authentication
- Notion OAuth connections with encrypted token storage
- Session and cache management
- Thread management for cross-device continuity
- Tool call journaling for partial failure recovery

Security: All OAuth tokens are encrypted at rest using Fernet encryption.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, ENUM, JSONB, UUID
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

    device_sessions: Mapped[list["DeviceSession"]] = relationship(
        "DeviceSession",
        back_populates="user",
        cascade="all, delete-orphan",
        doc="All device sessions for this user",
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email}, status={self.status})>"


class DeviceSession(Base):
    """
    Device session model for transport continuity and token metering.

    Stores device tokens (dtok_xxx) for request authentication, workspace binding,
    and usage tracking. Provides atomic operations for race-safe token validation
    and sliding window expiry with hard caps.

    Attributes:
        session_id: Unique session identifier (UUID)
        user_id: Owner user ID (foreign key to users.id)
        workspace_id: Active workspace for MCP routing (optional)
        device_token_hash: SHA-256 hash of device token (32 bytes)
        created_at: Session creation timestamp
        last_accessed: Last request timestamp (for sliding expiry)
        expires_at: Current expiry time (sliding 7-day window)
        hard_expires_at: Absolute expiry cap (30-day maximum)
        tokens_input_total: Total input tokens consumed
        tokens_output_total: Total output tokens generated
        request_count: Number of requests made with this session
        revoked_at: Revocation timestamp (soft delete)
    """

    __tablename__ = "device_sessions"

    # Primary key
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        doc="Unique device session identifier",
    )

    # Foreign key to users table
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="Owner user ID",
    )

    # Workspace binding for MCP routing
    workspace_id: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc="Active workspace for MCP tool routing",
    )

    # Secure token storage (SHA-256 hash only)
    device_token_hash: Mapped[bytes] = mapped_column(
        LargeBinary(32),  # Exactly SHA-256 size
        unique=True,
        nullable=False,
        doc="SHA-256 hash of device token (dtok_xxx)",
    )

    # Timestamp fields with proper indexing
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        doc="Session creation timestamp",
    )

    last_accessed: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
        index=True,  # For cleanup and sliding expiry queries
        doc="Last request timestamp",
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,  # For validation queries
        doc="Current expiry time (sliding 7-day window)",
    )

    hard_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,  # For cleanup queries
        doc="Absolute expiry cap (30-day maximum)",
    )

    # Usage tracking and metering
    tokens_input_total: Mapped[int] = mapped_column(
        Integer,
        server_default="0",
        nullable=False,
        doc="Total input tokens consumed",
    )

    tokens_output_total: Mapped[int] = mapped_column(
        Integer,
        server_default="0",
        nullable=False,
        doc="Total output tokens generated",
    )

    request_count: Mapped[int] = mapped_column(
        Integer,
        server_default="0",
        nullable=False,
        doc="Number of requests made with this session",
    )

    # Soft deletion capability
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Revocation timestamp (soft delete)",
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="device_sessions",
        doc="User who owns this device session",
    )

    def __repr__(self) -> str:
        return f"<DeviceSession(id={self.session_id}, user_id={self.user_id}, workspace={self.workspace_id})>"


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
        supports_refresh: Whether connection supports token refresh
        last_refresh_attempt: Timestamp of last refresh attempt
        refresh_failure_count: Number of consecutive refresh failures
        needs_reauth: Whether re-authentication is required
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

    workspace_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, doc="Human-friendly Notion workspace name"
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

    # Token refresh tracking (Phase 1 - Issue #16)
    supports_refresh: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        doc="Whether this connection supports token refresh (has valid refresh_token)",
    )

    last_refresh_attempt: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Timestamp of last refresh attempt (success or failure)",
    )

    refresh_failure_count: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        doc="Number of consecutive refresh failures (reset to 0 on success)",
    )

    needs_reauth: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        doc="Whether this connection requires user re-authentication due to terminal refresh errors",
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

    # Token refresh helper methods (Phase 1 - Issue #16)
    @property
    def is_refresh_capable(self) -> bool:
        """Check if this connection can perform token refresh."""
        return (
            self.supports_refresh
            and self.refresh_token_ciphertext is not None
            and not self.needs_reauth
            and self.is_active
        )

    @property
    def refresh_failure_threshold_exceeded(self) -> bool:
        """Check if refresh failures exceed threshold (3 consecutive failures)."""
        return self.refresh_failure_count >= 3

    def mark_refresh_success(self) -> None:
        """Mark a successful token refresh operation."""
        self.last_refresh_attempt = datetime.now(timezone.utc)
        self.refresh_failure_count = 0
        self.needs_reauth = False

    def mark_refresh_failure(self, is_terminal_error: bool = False) -> None:
        """
        Mark a failed token refresh operation.

        Args:
            is_terminal_error: Whether this is a terminal error requiring re-auth
        """
        self.last_refresh_attempt = datetime.now(timezone.utc)
        self.refresh_failure_count += 1

        # Mark for re-auth on terminal errors or after threshold failures
        if is_terminal_error or self.refresh_failure_threshold_exceeded:
            self.needs_reauth = True

    def update_refresh_capability(self, has_refresh_token: bool) -> None:
        """
        Update refresh capability based on token response analysis.

        Args:
            has_refresh_token: Whether the connection has a valid refresh token
        """
        self.supports_refresh = (
            has_refresh_token and self.refresh_token_ciphertext is not None
        )

    def __repr__(self) -> str:
        status = "active" if self.is_active else "revoked"
        return (
            f"<NotionConnection(id={self.id}, user_id={self.user_id}, "
            f"workspace_id={self.workspace_id}, status={status})>"
        )


class OAuthState(Base):
    """
    OAuth state management for CSRF protection and user binding.

    Stores cryptographically secure state tokens with TTL for OAuth flows.
    Each state is bound to a flow session and includes optional return_to URL.

    Security features:
    - Cryptographically random state tokens
    - Flow session binding to prevent CSRF attacks
    - TTL expiration (typically 10-15 minutes)
    - One-time use enforcement

    Attributes:
        id: Unique state identifier (UUID)
        state: Cryptographically random state token
        user_id: Optional user ID for authenticated flows
        flow_session_id: Flow session identifier for OAuth CSRF protection
        provider: OAuth provider (notion, github, etc.)
        return_to: Optional return URL after successful auth
        created_at: State creation timestamp
        expires_at: State expiration timestamp (TTL enforcement)
        used_at: Usage timestamp (NULL = unused, set when consumed)
    """

    __tablename__ = "oauth_states"

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc="Unique state identifier",
    )

    # State token (cryptographically random)
    state: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
        doc="Cryptographically random state token for CSRF protection",
    )

    # User and flow session binding
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        doc="Optional user ID for authenticated flows",
    )

    flow_session_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        doc="Flow session identifier for OAuth CSRF protection",
    )

    # OAuth provider
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False, doc="OAuth provider (notion, github, etc.)"
    )

    # Return URL for post-auth navigation
    return_to: Mapped[Optional[str]] = mapped_column(
        String(2048),
        nullable=True,
        doc="Optional return URL after successful authentication",
    )

    # Timestamps for TTL and usage tracking
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        doc="State creation timestamp",
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc="State expiration timestamp (TTL enforcement)",
    )

    used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, doc="Usage timestamp (NULL = unused)"
    )

    # Indexes and constraints
    __table_args__ = (
        # Index on state token for fast lookups
        Index("ix_oauth_state_token", "state"),
        # Index on expiration for cleanup queries
        Index("ix_oauth_state_expires", "expires_at"),
        # Index on provider + created_at for analytics
        Index("ix_oauth_state_provider_created", "provider", "created_at"),
    )

    @property
    def is_expired(self) -> bool:
        """Check if this state token is expired."""
        return datetime.now(timezone.utc) >= self.expires_at

    @property
    def is_used(self) -> bool:
        """Check if this state token has been used."""
        return self.used_at is not None

    @property
    def is_valid(self) -> bool:
        """Check if this state token is valid (not expired and not used)."""
        return not self.is_expired and not self.is_used

    def mark_used(self) -> None:
        """Mark this state token as used."""
        self.used_at = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        status = (
            "valid" if self.is_valid else ("expired" if self.is_expired else "used")
        )
        return (
            f"<OAuthState(id={self.id}, provider={self.provider}, "
            f"state={self.state[:8]}..., status={status})>"
        )


class Thread(Base):
    """
    Conversation thread for cross-device continuity.

    Supports conversation state persistence across devices and sessions.
    Threads can be accessed via ID or share tokens with TTL.

    Attributes:
        id: Unique thread identifier
        owner_user_id: Optional owner (defaults to system user in MVP)
        workspace_id: Optional workspace binding for tool routing
        share_token_hash: Hashed share token for cross-device access
        share_token_expires_at: Share token expiration timestamp
        created_at: Thread creation timestamp
        last_activity_at: Last message/activity timestamp
        deleted_at: Soft delete timestamp (NULL = active)
    """

    __tablename__ = "threads"

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
        doc="Unique thread identifier",
    )

    # Ownership and workspace binding
    owner_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,  # Set from env var in code for MVP
        doc="Thread owner (defaults to system user in MVP)",
    )

    workspace_id: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, doc="Optional workspace for tool routing"
    )

    # Optional metadata for thread organization
    title: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, doc="Human-readable thread title"
    )

    thread_metadata: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, doc="Flexible metadata storage for future extensions"
    )

    # Share token for cross-device access
    share_token_hash: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary,
        nullable=True,
        unique=True,
        doc="SHA256 hash of share token for cross-device access",
    )

    share_token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Share token expiration timestamp",
    )

    # Activity tracking
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="Thread creation timestamp",
    )

    last_activity_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, doc="Last message/activity timestamp"
    )

    # Soft delete
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, doc="Soft delete timestamp"
    )

    # Relationships
    messages: Mapped[list["ThreadMessage"]] = relationship(
        "ThreadMessage",
        back_populates="thread",
        cascade="all, delete-orphan",
        doc="All messages in this thread",
    )

    tool_calls: Mapped[list["ToolCallLog"]] = relationship(
        "ToolCallLog",
        back_populates="thread",
        cascade="all, delete-orphan",
        doc="All tool calls made in this thread",
    )

    @property
    def is_active(self) -> bool:
        """Check if thread is active (not deleted)."""
        return self.deleted_at is None

    @property
    def is_share_token_valid(self) -> bool:
        """Check if share token is still valid."""
        if not self.share_token_expires_at:
            return True  # No expiration set
        return datetime.now(timezone.utc) < self.share_token_expires_at

    def __repr__(self) -> str:
        return f"<Thread(id={self.id}, workspace={self.workspace_id}, active={self.is_active})>"


class ThreadMessage(Base):
    """
    Individual message within a thread.

    Stores user, assistant, tool, and system messages with idempotency support.
    Content is stored as JSONB for flexibility with different message types.

    Attributes:
        id: Unique message identifier
        thread_id: Parent thread ID
        role: Message role (user/assistant/tool/system)
        content: Message content (JSONB for flexibility)
        client_message_id: Client-provided ID for idempotency
        in_reply_to: Previous message ID for threading
        status: Message status (pending/streaming/complete/error)
        tool_calls: Tool calls made in this message
        tokens_input: Input tokens consumed
        tokens_output: Output tokens generated
        created_at: Message creation timestamp
    """

    __tablename__ = "thread_messages"

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
        doc="Unique message identifier",
    )

    # Thread relationship
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
        doc="Parent thread ID",
    )

    # Request tracking for debugging
    request_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        doc="Request ID for tracing and debugging",
    )

    # Message content
    role: Mapped[str] = mapped_column(
        Text, nullable=False, doc="Message role (user/assistant/tool/system)"
    )

    content: Mapped[dict] = mapped_column(
        JSONB, nullable=False, doc="Message content in flexible JSON format"
    )

    # Idempotency
    client_message_id: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, index=True, doc="Client-provided ID for idempotency"
    )

    # Threading
    in_reply_to: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("thread_messages.id", ondelete="SET NULL"),
        nullable=True,
        doc="Previous message ID for conversation threading",
    )

    # Status tracking
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="complete",
        doc="Message status (pending/streaming/complete/error)",
    )

    # Tool calls
    tool_calls: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, doc="Tool calls made during this message"
    )

    # Token tracking
    tokens_input: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, doc="Input tokens consumed"
    )

    tokens_output: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, doc="Output tokens generated"
    )

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="Message creation timestamp",
    )

    # Relationships
    thread: Mapped["Thread"] = relationship(
        "Thread", back_populates="messages", doc="Parent thread"
    )

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'tool', 'system')",
            name="chk_message_role",
        ),
        CheckConstraint(
            "status IN ('pending', 'streaming', 'complete', 'error')",
            name="chk_message_status",
        ),
        # Unique constraint for client_message_id per thread (handled in migration with partial index)
    )

    def __repr__(self) -> str:
        return (
            f"<ThreadMessage(id={self.id}, thread_id={self.thread_id}, "
            f"role={self.role}, status={self.status})>"
        )


class ToolCallLog(Base):
    """
    Log of tool calls for idempotency and partial failure recovery.

    Tracks every tool execution attempt, enabling recovery from partial failures
    where tools succeed but the LLM response fails. Uses idempotency keys to
    prevent duplicate executions.

    Attributes:
        id: Unique log entry identifier
        request_id: Request ID for correlation
        thread_id: Thread containing this tool call
        message_id: Message containing this tool call (may be NULL)
        call_index: Position in tool call sequence
        idempotency_key: Unique key to prevent duplicate executions
        tool_name: Name of tool being called
        args: Tool arguments (JSONB)
        result_digest: Hash of result for cache invalidation
        status: Execution status (pending/success/failed)
        error: Error message if failed
        started_at: Execution start timestamp
        finished_at: Execution completion timestamp
    """

    __tablename__ = "tool_call_log"

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
        doc="Unique log entry identifier",
    )

    # Request correlation
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
        doc="Request ID for correlation",
    )

    # Thread relationship
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="Thread containing this tool call",
    )

    # Message relationship (may be NULL if LLM fails before creating message)
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("thread_messages.id", ondelete="SET NULL"),
        nullable=True,
        doc="Message containing this tool call (may be NULL)",
    )

    # Execution tracking
    call_index: Mapped[int] = mapped_column(
        Integer, nullable=False, doc="Position in tool call sequence"
    )

    idempotency_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
        index=True,
        doc="Unique key to prevent duplicate executions",
    )

    tool_name: Mapped[str] = mapped_column(
        Text, nullable=False, doc="Name of tool being called"
    )

    args: Mapped[dict] = mapped_column(JSONB, nullable=False, doc="Tool arguments")

    # Result tracking
    result_digest: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, doc="Hash of result for cache invalidation"
    )

    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="pending",
        doc="Execution status (pending/success/failed)",
    )

    error: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, doc="Error message if failed"
    )

    # Timing
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        doc="Execution start timestamp",
    )

    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, doc="Execution completion timestamp"
    )

    # Relationships
    thread: Mapped["Thread"] = relationship(
        "Thread", back_populates="tool_calls", doc="Parent thread"
    )

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'success', 'failed')", name="chk_tool_status"
        ),
    )

    @property
    def duration_ms(self) -> Optional[float]:
        """Calculate execution duration in milliseconds."""
        if not self.finished_at:
            return None
        return (self.finished_at - self.started_at).total_seconds() * 1000

    def __repr__(self) -> str:
        return (
            f"<ToolCallLog(id={self.id}, tool={self.tool_name}, "
            f"status={self.status}, duration_ms={self.duration_ms})>"
        )
