# Threads-lite Implementation Guide (Issue #51)

## Overview
This document provides a detailed, step-by-step implementation plan for adding thread support to the Alfred Agent Core MVP. The implementation focuses on minimal changes to enable cross-device conversation continuity.

## Implementation Order

### Phase 1: Database Schema (30 mins)
Create and run migration for thread tables.

### Phase 2: Thread Service (2 hours)
Build core thread management service with message persistence.

### Phase 3: Chat Endpoint Enhancement (2 hours)
Modify existing `/chat` endpoint to support threads with backward compatibility.

### Phase 4: Tool Call Journaling (1 hour)
Add hooks for tracking tool execution and partial failure recovery.

### Phase 5: Testing & Verification (1 hour)
End-to-end testing of thread continuity and failure scenarios.

---

## Phase 1: Database Schema

### 1.1 Create Migration File

```bash
# Generate new migration
cd agent-core
alembic revision -m "add_threads_and_tool_logging"
```

### 1.2 Migration Content

```python
# agent-core/src/db/migrations/versions/xxx_add_threads_and_tool_logging.py
"""add_threads_and_tool_logging

Revision ID: xxx
Revises: b85f1c0aec2a
Create Date: 2025-01-XX
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "xxx"
down_revision: Union[str, None] = "b85f1c0aec2a"

def upgrade() -> None:
    """Create threads, thread_messages, and tool_call_log tables."""

    # Create required PostgreSQL extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")  # for gen_random_uuid()
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")    # if used elsewhere

    # Create threads table
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
            # No server default - set from env var in code
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
        # Soft delete support
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Create thread_messages table
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
        # For idempotency - client-provided ID
        sa.Column(
            "client_message_id",
            sa.Text(),
            nullable=True,
        ),
        # For conversation threading
        sa.Column(
            "in_reply_to",
            pg.UUID(as_uuid=True),
            nullable=True,
        ),
        # Status tracking
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="'complete'",
        ),
        # Tool calls made in this message
        sa.Column(
            "tool_calls",
            pg.JSONB(),
            nullable=True,
        ),
        # Token tracking
        sa.Column("tokens_input", sa.Integer(), nullable=True),
        sa.Column("tokens_output", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["threads.id"], ondelete="CASCADE"
        ),
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
            nullable=True,
        ),
        sa.Column(
            "call_index",
            sa.Integer(),
            nullable=False,
        ),
        # For idempotency
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
            server_default="'pending'",
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
        sa.ForeignKeyConstraint(
            ["thread_id"], ["threads.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["thread_messages.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'success', 'failed')",
            name="chk_tool_status",
        ),
    )

    # Create indexes
    op.create_index("idx_threads_owner", "threads", ["owner_user_id"])
    op.create_index("idx_threads_workspace", "threads", ["workspace_id"])
    op.create_index("idx_threads_activity", "threads", ["last_activity_at"])
    op.create_index("idx_threads_token_expires", "threads", ["share_token_expires_at"])

    # Optimized composite index for hot path (tail N by time)
    op.create_index("idx_msgs_thread_created", "thread_messages", ["thread_id", "created_at"])
    op.create_index("idx_messages_status", "thread_messages", ["status"])

    # Unique constraint for client idempotency (per thread)
    op.create_index(
        "uq_thread_client_msg",
        "thread_messages",
        ["thread_id", "client_message_id"],
        unique=True,
        postgresql_where=sa.text("client_message_id IS NOT NULL")
    )

    op.create_index("idx_tool_log_request", "tool_call_log", ["request_id"])
    op.create_index("idx_tool_log_thread", "tool_call_log", ["thread_id"])
    op.create_index("idx_tool_log_idempotency", "tool_call_log", ["idempotency_key"], unique=True)

def downgrade() -> None:
    """Drop thread-related tables."""
    op.drop_index("idx_tool_log_idempotency")
    op.drop_index("idx_tool_log_thread")
    op.drop_index("idx_tool_log_request")
    op.drop_table("tool_call_log")

    op.drop_index("idx_messages_status")
    op.drop_index("idx_messages_client_id")
    op.drop_index("idx_messages_thread")
    op.drop_table("thread_messages")

    op.drop_index("idx_threads_activity")
    op.drop_index("idx_threads_workspace")
    op.drop_index("idx_threads_owner")
    op.drop_table("threads")
```

### 1.3 SQLAlchemy Models

```python
# agent-core/src/db/models.py
# Add to existing models file

from sqlalchemy import (
    Column, String, Text, Integer, DateTime, ForeignKey,
    CheckConstraint, LargeBinary, Boolean
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

class Thread(Base):
    """Conversation thread for cross-device continuity."""
    __tablename__ = "threads"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    owner_user_id = Column(UUID(as_uuid=True), nullable=True)  # Set from env var in code
    workspace_id = Column(Text, nullable=True)
    share_token_hash = Column(LargeBinary, nullable=True, unique=True)
    share_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_activity_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    messages = relationship("ThreadMessage", back_populates="thread", cascade="all, delete-orphan")
    tool_calls = relationship("ToolCallLog", back_populates="thread", cascade="all, delete-orphan")


class ThreadMessage(Base):
    """Individual message within a thread."""
    __tablename__ = "thread_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    thread_id = Column(UUID(as_uuid=True), ForeignKey("threads.id", ondelete="CASCADE"), nullable=False)
    role = Column(Text, nullable=False)
    content = Column(JSONB, nullable=False)
    client_message_id = Column(Text, nullable=True, index=True)
    in_reply_to = Column(UUID(as_uuid=True), ForeignKey("thread_messages.id", ondelete="SET NULL"), nullable=True)
    status = Column(Text, nullable=False, default="complete")
    tool_calls = Column(JSONB, nullable=True)
    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    thread = relationship("Thread", back_populates="messages")

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'tool', 'system')", name="chk_message_role"),
        CheckConstraint("status IN ('pending', 'streaming', 'complete', 'error')", name="chk_message_status"),
    )


class ToolCallLog(Base):
    """Log of tool calls for idempotency and partial failure recovery."""
    __tablename__ = "tool_call_log"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    request_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    thread_id = Column(UUID(as_uuid=True), ForeignKey("threads.id", ondelete="CASCADE"), nullable=False, index=True)
    message_id = Column(UUID(as_uuid=True), ForeignKey("thread_messages.id", ondelete="CASCADE"), nullable=True)
    call_index = Column(Integer, nullable=False)
    idempotency_key = Column(Text, nullable=False, unique=True, index=True)
    tool_name = Column(Text, nullable=False)
    args = Column(JSONB, nullable=False)
    result_digest = Column(Text, nullable=True)
    status = Column(Text, nullable=False, default="pending")
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    thread = relationship("Thread", back_populates="tool_calls")

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'success', 'failed')", name="chk_tool_status"),
    )
```

---

## Phase 2: Thread Service

### 2.1 Thread Service Implementation

```python
# agent-core/src/services/thread_service.py
"""
Thread management service for conversation continuity.

Handles thread creation, message persistence, and cross-device access.
"""

import hashlib
import json
import secrets
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime, timedelta

from sqlalchemy import select, and_, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import Thread, ThreadMessage, ToolCallLog
from src.config import get_settings
from src.utils.logging import get_logger
from src.utils.crypto import CryptoService

logger = get_logger(__name__)


class ThreadService:
    """
    Manages conversation threads with cross-device continuity.

    Key responsibilities:
    - Thread creation and retrieval
    - Message persistence with idempotency
    - Share token generation and validation
    - Tool call journaling for partial failure recovery
    """

    def __init__(self, crypto_service: CryptoService):
        """
        Initialize thread service.

        Args:
            crypto_service: For share token hashing
        """
        self.crypto = crypto_service

    async def find_or_create_thread(
        self,
        db: AsyncSession,
        thread_id: Optional[str] = None,
        share_token: Optional[str] = None,
        user_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Thread:
        """
        Find existing thread or create new one.

        Priority order:
        1. Explicit thread_id
        2. Share token lookup
        3. Create new thread

        Args:
            db: Database session
            thread_id: Explicit thread ID if resuming
            share_token: Share token for cross-device access
            user_id: Owner user ID (uses default for MVP)
            workspace_id: Optional workspace binding

        Returns:
            Thread object (existing or newly created)
        """
        # Try to find existing thread
        thread = None

        if thread_id:
            # Direct lookup by ID
            stmt = select(Thread).where(
                and_(
                    Thread.id == thread_id,
                    Thread.deleted_at.is_(None)
                )
            )
            result = await db.execute(stmt)
            thread = result.scalar_one_or_none()

            if thread:
                logger.info(
                    "Found thread by ID",
                    thread_id=thread_id,
                    message_count=len(thread.messages) if thread.messages else 0
                )

        elif share_token:
            # Lookup by share token with expiry check
            token_hash = hashlib.sha256(share_token.encode()).digest()
            stmt = select(Thread).where(
                and_(
                    Thread.share_token_hash == token_hash,
                    Thread.deleted_at.is_(None),
                    or_(
                        Thread.share_token_expires_at.is_(None),
                        Thread.share_token_expires_at > func.now()
                    )
                )
            )
            result = await db.execute(stmt)
            thread = result.scalar_one_or_none()

            if thread:
                logger.info(
                    "Found thread by share token",
                    thread_id=str(thread.id),
                    has_token=True
                )

        # Create new thread if not found
        if not thread:
            settings = get_settings()
            thread = Thread(
                owner_user_id=user_id or UUID(settings.default_user_id),
                workspace_id=workspace_id,
                last_activity_at=datetime.utcnow()
            )
            db.add(thread)
            await db.flush()  # Get the ID

            logger.info(
                "Created new thread",
                thread_id=str(thread.id),
                workspace_id=workspace_id
            )

        # Update last activity
        thread.last_activity_at = datetime.utcnow()

        return thread

    async def add_message(
        self,
        db: AsyncSession,
        thread: Thread,
        role: str,
        content: Any,
        client_message_id: Optional[str] = None,
        in_reply_to: Optional[UUID] = None,
        status: str = "complete",
        tool_calls: Optional[List[Dict]] = None,
        tokens: Optional[Dict[str, int]] = None,
    ) -> ThreadMessage:
        """
        Add message to thread with idempotency check.

        Args:
            db: Database session
            thread: Thread to add message to
            role: Message role (user/assistant/tool/system)
            content: Message content (string or structured)
            client_message_id: Client-provided ID for idempotency
            in_reply_to: Previous message ID for threading
            status: Message status (pending/streaming/complete/error)
            tool_calls: Tool calls made in this message
            tokens: Token usage dict with input/output counts

        Returns:
            ThreadMessage object (existing if duplicate, new otherwise)
        """
        # Check for duplicate by client_message_id
        if client_message_id:
            stmt = select(ThreadMessage).where(
                and_(
                    ThreadMessage.thread_id == thread.id,
                    ThreadMessage.client_message_id == client_message_id
                )
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                logger.info(
                    "Duplicate message detected",
                    thread_id=str(thread.id),
                    client_message_id=client_message_id,
                    message_id=str(existing.id)
                )
                return existing

        # Create new message
        message = ThreadMessage(
            thread_id=thread.id,
            role=role,
            content=content if isinstance(content, dict) else {"text": content},
            client_message_id=client_message_id,
            in_reply_to=in_reply_to,
            status=status,
            tool_calls=tool_calls,
            tokens_input=tokens.get("input") if tokens else None,
            tokens_output=tokens.get("output") if tokens else None,
        )

        db.add(message)
        await db.flush()

        logger.info(
            "Added message to thread",
            thread_id=str(thread.id),
            message_id=str(message.id),
            role=role,
            status=status,
            has_tools=bool(tool_calls)
        )

        return message

    async def get_thread_messages(
        self,
        db: AsyncSession,
        thread_id: UUID,
        limit: int = 100,
        include_tool_calls: bool = True
    ) -> List[ThreadMessage]:
        """
        Get messages for a thread.

        Args:
            db: Database session
            thread_id: Thread ID
            limit: Maximum messages to return
            include_tool_calls: Whether to include tool call details

        Returns:
            List of messages in chronological order
        """
        stmt = select(ThreadMessage).where(
            ThreadMessage.thread_id == thread_id
        ).order_by(
            ThreadMessage.created_at.asc()
        ).limit(limit)

        result = await db.execute(stmt)
        messages = result.scalars().all()

        logger.debug(
            "Retrieved thread messages",
            thread_id=str(thread_id),
            count=len(messages),
            limit=limit
        )

        return messages

    async def generate_share_token(
        self,
        db: AsyncSession,
        thread: Thread
    ) -> str:
        """
        Generate share token for cross-device access with expiry.

        Args:
            db: Database session
            thread: Thread to generate token for

        Returns:
            Share token string (thr_<base64url>)
        """
        settings = get_settings()

        # Generate secure random token
        token_bytes = secrets.token_urlsafe(32)
        token = f"thr_{token_bytes}"

        # Store hash in database with expiry
        token_hash = hashlib.sha256(token.encode()).digest()
        thread.share_token_hash = token_hash
        thread.share_token_expires_at = datetime.utcnow() + timedelta(
            hours=settings.share_token_ttl_hours
        )

        await db.flush()

        logger.info(
            "Generated share token",
            thread_id=str(thread.id),
            token_prefix=token[:12]
        )

        return token

    async def log_tool_call(
        self,
        db: AsyncSession,
        request_id: str,
        thread_id: UUID,
        message_id: Optional[UUID],
        user_message_id: UUID,
        call_index: int,
        tool_name: str,
        args: Dict[str, Any],
        idempotency_key: Optional[str] = None
    ) -> ToolCallLog:
        """
        Log tool call for idempotency and failure recovery.

        Args:
            db: Database session
            request_id: Current request ID
            thread_id: Thread ID
            message_id: Message ID (if available)
            user_message_id: User message ID for stable idempotency
            call_index: Index in tool call sequence
            tool_name: Name of tool being called
            args: Tool arguments
            idempotency_key: Optional explicit idempotency key

        Returns:
            ToolCallLog entry
        """
        # Generate stable idempotency key if not provided
        if not idempotency_key:
            # Canonical JSON for deterministic serialization
            canonical_args = json.dumps(args, separators=(",", ":"), sort_keys=True)
            key_data = f"{request_id}:{thread_id}:{user_message_id}:{tool_name}:{canonical_args}:{call_index}"
            idempotency_key = hashlib.sha256(key_data.encode()).hexdigest()

        # Check for existing call
        stmt = select(ToolCallLog).where(
            ToolCallLog.idempotency_key == idempotency_key
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            logger.info(
                "Tool call already executed",
                tool_name=tool_name,
                status=existing.status,
                idempotency_key=idempotency_key
            )
            return existing

        # Create new log entry
        log_entry = ToolCallLog(
            request_id=request_id,
            thread_id=thread_id,
            message_id=message_id,
            call_index=call_index,
            idempotency_key=idempotency_key,
            tool_name=tool_name,
            args=args,
            status="pending"
        )

        db.add(log_entry)
        await db.flush()

        logger.info(
            "Logged tool call",
            tool_name=tool_name,
            thread_id=str(thread_id),
            call_index=call_index
        )

        return log_entry

    async def update_tool_call_status(
        self,
        db: AsyncSession,
        log_entry: ToolCallLog,
        status: str,
        result_digest: Optional[str] = None,
        error: Optional[str] = None
    ) -> None:
        """
        Update tool call status after execution.

        Args:
            db: Database session
            log_entry: Tool call log entry
            status: New status (success/failed)
            result_digest: Optional result hash for cache invalidation
            error: Error message if failed
        """
        log_entry.status = status
        log_entry.finished_at = datetime.utcnow()

        if result_digest:
            log_entry.result_digest = result_digest

        if error:
            log_entry.error = error[:1000]  # Truncate long errors

        await db.flush()

        logger.info(
            "Updated tool call status",
            tool_name=log_entry.tool_name,
            status=status,
            duration_ms=(log_entry.finished_at - log_entry.started_at).total_seconds() * 1000
        )
```

---

## Phase 3: Chat Endpoint Enhancement

### 3.1 Updated Request Models

```python
# agent-core/src/routers/chat.py
# Update existing models

from typing import Optional
from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    """
    Enhanced chat request with thread support.

    Maintains backward compatibility while adding thread features.
    """
    messages: List[Message] = Field(
        ...,
        description="New messages to add (usually just one)",
        min_length=1
    )
    session: Optional[str] = Field(
        None,
        description="Session token for metering (not conversation state)"
    )
    forceRefresh: bool = Field(
        False,
        description="Force cache bypass for fresh results"
    )

    # Thread support (new fields)
    threadId: Optional[str] = Field(
        None,
        description="Resume existing thread by ID"
    )
    shareToken: Optional[str] = Field(
        None,
        description="Resume thread via share token (thr_xxx format)"
    )
    clientMessageId: Optional[str] = Field(
        None,
        description="Client-provided ID for idempotency"
    )
    returnShareToken: bool = Field(
        False,
        description="Generate and return share token for cross-device access"
    )


class ChatResponse(BaseModel):
    """Enhanced response with thread information."""
    reply: str = Field(..., description="Agent's response")
    meta: ResponseMeta = Field(..., description="Response metadata")

    # Thread information (new fields)
    threadId: Optional[str] = Field(
        None,
        description="Thread ID for continuation"
    )
    shareToken: Optional[str] = Field(
        None,
        description="Share token if requested"
    )
```

### 3.2 Request ID Middleware

```python
# agent-core/src/middleware.py
from uuid import uuid4
from fastapi import Request

@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Always inject request ID for tracing."""
    request.state.request_id = str(uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response
```

### 3.3 Enhanced Chat Endpoint

```python
# agent-core/src/routers/chat.py
# Replace existing chat_endpoint function

from src.services.thread_service import ThreadService
from src.services.cache_service import CacheService
from src.db.session import get_db

def normalize_message_content(message: dict) -> dict:
    """Ensure consistent content shape: always {"text": ...}."""
    if isinstance(message.get("content"), str):
        message["content"] = {"text": message["content"]}
    return message

@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    request: Request,
    chat_request: ChatRequest,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    """
    Enhanced chat endpoint with thread support and partial failure handling.

    Key features:
    - Thread continuity across devices
    - Idempotent message handling
    - Tool call journaling for failure recovery
    - Cache scoped to {user}:{workspace}

    Flow:
    1. Find or create thread
    2. Save user message immediately (before LLM)
    3. Fetch thread history if continuing
    4. Execute with tool call journaling
    5. Save assistant response
    6. Return with thread info
    """
    # Get request ID from middleware (always present)
    request_id = request.state.request_id

    # Initialize services
    settings = get_settings()
    crypto_service = CryptoService(settings.fernet_key)
    thread_service = ThreadService(crypto_service)
    cache_service = CacheService()

    # Extract user context
    user_id = request.headers.get("X-User-ID")
    if not user_id and chat_request.session:
        user_id = chat_request.session  # Fallback for MVP

    # Initial workspace from session/headers (may be overridden by thread)
    workspace_id_from_session = request.headers.get("X-Workspace-ID")

    logger.info(
        "Thread-aware chat request",
        request_id=request_id,
        has_thread_id=bool(chat_request.threadId),
        has_share_token=bool(chat_request.shareToken),
        has_client_msg_id=bool(chat_request.clientMessageId),
        message_count=len(chat_request.messages)
    )

    # Track tool calls for partial failure recovery
    executed_tool_calls = []
    assistant_message_id = None

    try:
        # 1. Find or create thread
        thread = await thread_service.find_or_create_thread(
            db=db,
            thread_id=chat_request.threadId,
            share_token=chat_request.shareToken,
            user_id=user_id,
            workspace_id=workspace_id_from_session
        )

        # Determine effective workspace (thread takes precedence)
        workspace_id = thread.workspace_id or workspace_id_from_session

        # 2. Save user message IMMEDIATELY (before any LLM calls)
        # This ensures we have a record even if everything else fails
        user_message = chat_request.messages[-1]  # Take last message (delta pattern)
        user_message = normalize_message_content(user_message)

        saved_user_msg = await thread_service.add_message(
            db=db,
            thread=thread,
            role=user_message.role,
            content=user_message.content,
            client_message_id=chat_request.clientMessageId,
            status="complete"
        )

        # Commit user message immediately
        await db.commit()

        # 3. Fetch thread history if continuing
        message_history = []
        if chat_request.threadId or chat_request.shareToken:
            # Get previous messages (excluding the one we just added)
            previous_messages = await thread_service.get_thread_messages(
                db=db,
                thread_id=thread.id,
                limit=50  # Reasonable context window
            )

            # Convert to format expected by agent
            for msg in previous_messages:
                if msg.id != saved_user_msg.id:  # Skip duplicate
                    message_history.append({
                        "role": msg.role,
                        "content": msg.content.get("text", "")
                    })

        # Add current message to history
        message_history.append({
            "role": user_message.role,
            "content": user_message.content
        })

        # 4. Get agent orchestrator with tool call hook
        orchestrator = await get_agent_orchestrator()

        # Create tool call hooks for journaling
        async def before_tool_call(tool_name: str, args: dict, index: int):
            """Journal tool execution before calling for idempotency."""
            log_entry = await thread_service.log_tool_call(
                db=db,
                request_id=request_id,
                thread_id=thread.id,
                message_id=assistant_message_id,  # May be None initially
                user_message_id=saved_user_msg.id,  # For stable idempotency
                call_index=index,
                tool_name=tool_name,
                args=args
            )

            # Commit immediately so it persists even if LLM fails
            await db.commit()

            return log_entry

        async def after_tool_call(tool_name: str, args: dict, index: int, result: Any = None, error: str = None):
            """Update tool status after execution."""
            # Find the log entry
            stmt = select(ToolCallLog).where(
                and_(
                    ToolCallLog.thread_id == thread.id,
                    ToolCallLog.call_index == index,
                    ToolCallLog.tool_name == tool_name
                )
            ).order_by(ToolCallLog.created_at.desc())
            result = await db.execute(stmt)
            log_entry = result.scalar_one_or_none()

            if log_entry:
                status = "success" if error is None else "failed"
                result_digest = hashlib.sha256(str(result).encode()).hexdigest() if result else None
                await thread_service.update_tool_call_status(
                    db=db,
                    log_entry=log_entry,
                    status=status,
                    result_digest=result_digest,
                    error=error
                )
                await db.commit()

                # Track successful calls
                if status == "success":
                    executed_tool_calls.append({
                        "tool": tool_name,
                        "args": args,
                        "log_id": str(log_entry.id)
                    })

                    # Invalidate cache immediately after successful tool execution
                    cache_key = cache_service.generate_key(
                        user_id=user_id or settings.default_user_id,
                        workspace_id=workspace_id,
                        tool=tool_name,
                        args=args
                    )
                    await cache_service.invalidate(cache_key)

        # Configure orchestrator with hooks
        orchestrator.set_before_tool_hook(before_tool_call)
        orchestrator.set_after_tool_hook(after_tool_call)

        # 5. Execute with LLM
        try:
            result = await orchestrator.chat(
                prompt=user_message.content,
                message_history=message_history,
                session_id=chat_request.session,
                user_id=user_id,
                workspace_id=workspace_id,
                force_refresh=chat_request.forceRefresh
            )

            # 6. Save assistant response
            assistant_msg = await thread_service.add_message(
                db=db,
                thread=thread,
                role="assistant",
                content=result.reply,
                in_reply_to=saved_user_msg.id,
                status="complete",
                tool_calls=executed_tool_calls if executed_tool_calls else None,
                tokens={
                    "input": result.meta.get("usage", {}).get("input_tokens", 0),
                    "output": result.meta.get("usage", {}).get("output_tokens", 0)
                }
            )
            assistant_message_id = assistant_msg.id

            await db.commit()

            # 7. Generate share token if requested
            share_token = None
            if chat_request.returnShareToken:
                share_token = await thread_service.generate_share_token(db, thread)
                await db.commit()

            # 8. Return enhanced response
            return ChatResponse(
                reply=result.reply,
                meta=ResponseMeta(
                    cacheHit=result.meta.get("cache_hit", False),
                    cacheTtlRemaining=result.meta.get("cache_ttl"),
                    tokens=TokenUsage(
                        input=result.meta.get("usage", {}).get("input_tokens", 0),
                        output=result.meta.get("usage", {}).get("output_tokens", 0)
                    ),
                    requestId=request_id
                ),
                threadId=str(thread.id),
                shareToken=share_token
            )

        except Exception as llm_error:
            # LLM failed but we may have successful tool calls
            logger.error(
                "LLM execution failed after tool calls",
                request_id=request_id,
                thread_id=str(thread.id),
                tool_calls_executed=len(executed_tool_calls),
                error=str(llm_error)
            )

            # Save error message with tool call record
            error_msg = await thread_service.add_message(
                db=db,
                thread=thread,
                role="assistant",
                content={
                    "error": str(llm_error),
                    "partial_success": len(executed_tool_calls) > 0
                },
                in_reply_to=saved_user_msg.id,
                status="error",
                tool_calls=executed_tool_calls if executed_tool_calls else None
            )

            await db.commit()

            # Re-raise with context
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": "PARTIAL_FAILURE" if executed_tool_calls else "LLM_ERROR",
                    "message": str(llm_error),
                    "requestId": request_id,
                    "threadId": str(thread.id),
                    "toolCallsExecuted": len(executed_tool_calls)
                }
            )

    except HTTPException:
        raise  # Re-raise HTTP exceptions

    except Exception as e:
        # Unexpected error - still try to save state
        logger.error(
            "Chat endpoint critical failure",
            request_id=request_id,
            error=str(e),
            error_type=type(e).__name__
        )

        # Try to save error state if we have a thread
        if 'thread' in locals():
            try:
                await thread_service.add_message(
                    db=db,
                    thread=thread,
                    role="system",
                    content=f"System error: {str(e)}",
                    status="error"
                )
                await db.commit()
            except:
                pass  # Best effort

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "SYSTEM_ERROR",
                "message": "An unexpected error occurred",
                "requestId": request_id
            }
        )
```

---

## Phase 4: Tool Call Journaling

### 4.1 Agent Orchestrator Hook Integration

```python
# agent-core/src/services/agent_orchestrator.py
# Add to existing orchestrator

from typing import Callable, Optional, Any

class AgentOrchestrator:
    """Enhanced orchestrator with tool call hooks."""

    def __init__(self, ...):
        # Existing initialization
        self.before_tool_hook: Optional[Callable] = None
        self.after_tool_hook: Optional[Callable] = None

    def set_before_tool_hook(self, hook: Callable) -> None:
        """
        Set hook to be called before each tool execution.

        Args:
            hook: Async function(tool_name, args, index) -> Any
        """
        self.before_tool_hook = hook

    def set_after_tool_hook(self, hook: Callable) -> None:
        """
        Set hook to be called after each tool execution.

        Args:
            hook: Async function(tool_name, args, index, result, error) -> None
        """
        self.after_tool_hook = hook

    async def _execute_tool(self, tool_name: str, args: dict, index: int = 0) -> Any:
        """
        Execute tool with hooks for journaling and recovery.

        Args:
            tool_name: Name of tool to execute
            args: Tool arguments
            index: Position in tool call sequence

        Returns:
            Tool execution result
        """
        # Before hook (for journaling)
        if self.before_tool_hook:
            try:
                await self.before_tool_hook(tool_name, args, index)
            except Exception as e:
                logger.warning(
                    "Before tool hook failed",
                    tool=tool_name,
                    error=str(e)
                )
                # Continue anyway - hook failure shouldn't block execution

        # Execute actual tool
        result = None
        error = None
        try:
            result = await self.mcp_router.call_tool(tool_name, args)
        except Exception as e:
            error = str(e)
            raise
        finally:
            # After hook (for status update)
            if self.after_tool_hook:
                try:
                    await self.after_tool_hook(tool_name, args, index, result, error)
                except Exception as e:
                    logger.warning(
                        "After tool hook failed",
                        tool=tool_name,
                        error=str(e)
                    )

        return result
```

---

## Phase 5: Testing & Verification

### 5.1 Test Scenarios

```python
# agent-core/tests/test_threads.py
"""
Test thread functionality and failure scenarios.
"""

import pytest
from httpx import AsyncClient
from unittest.mock import patch, MagicMock

@pytest.mark.asyncio
async def test_thread_creation(client: AsyncClient):
    """Test new thread creation."""
    response = await client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "returnShareToken": True
        },
        headers={"X-API-Key": "test-key"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["threadId"]
    assert data["shareToken"]
    assert data["shareToken"].startswith("thr_")


@pytest.mark.asyncio
async def test_thread_continuation(client: AsyncClient):
    """Test resuming thread with previous context."""
    # First message
    response1 = await client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "Remember the number 42"}]}
    )
    thread_id = response1.json()["threadId"]

    # Continue thread
    response2 = await client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "What number did I mention?"}],
            "threadId": thread_id
        }
    )

    # Should remember context
    assert "42" in response2.json()["reply"]


@pytest.mark.asyncio
async def test_idempotent_messages(client: AsyncClient):
    """Test duplicate message prevention."""
    request = {
        "messages": [{"role": "user", "content": "Test message"}],
        "clientMessageId": "unique-123"
    }

    # Send twice
    response1 = await client.post("/chat", json=request)
    response2 = await client.post("/chat", json=request)

    # Should get same response
    assert response1.json()["threadId"] == response2.json()["threadId"]
    # Verify only one message saved (would need DB check)


@pytest.mark.asyncio
async def test_partial_failure_recovery(client: AsyncClient):
    """Test tool execution with LLM failure."""
    with patch("src.services.agent_orchestrator.AgentOrchestrator.chat") as mock_chat:
        # Simulate tool success then LLM failure
        mock_chat.side_effect = Exception("Connection lost")

        response = await client.post(
            "/chat",
            json={
                "messages": [{"role": "user", "content": "Update my Notion page"}]
            }
        )

        assert response.status_code == 500
        error = response.json()
        assert error["error"] == "PARTIAL_FAILURE"
        assert error["toolCallsExecuted"] > 0


@pytest.mark.asyncio
async def test_share_token_access(client: AsyncClient):
    """Test cross-device access via share token."""
    # Create thread and get share token
    response1 = await client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "Initial message"}],
            "returnShareToken": True
        }
    )
    share_token = response1.json()["shareToken"]

    # Access from "different device" (no auth)
    response2 = await client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "Continue conversation"}],
            "shareToken": share_token
        }
    )

    assert response2.status_code == 200
    # Should have same thread ID
    assert response1.json()["threadId"] == response2.json()["threadId"]


@pytest.mark.asyncio
async def test_share_token_expiry(client: AsyncClient, db: AsyncSession):
    """Test share token expiration."""
    # Create thread with share token
    response = await client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "Test"}],
            "returnShareToken": True
        }
    )
    share_token = response.json()["shareToken"]
    thread_id = response.json()["threadId"]

    # Manually expire the token in DB
    stmt = update(Thread).where(Thread.id == thread_id).values(
        share_token_expires_at=datetime.utcnow() - timedelta(hours=1)
    )
    await db.execute(stmt)
    await db.commit()

    # Try to use expired token
    response2 = await client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "Should fail"}],
            "shareToken": share_token
        }
    )

    # Should create new thread since token is expired
    assert response2.json()["threadId"] != thread_id


@pytest.mark.asyncio
async def test_workspace_precedence(client: AsyncClient):
    """Test that thread workspace takes precedence."""
    # Create thread with workspace A
    response1 = await client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "Test"}]},
        headers={"X-Workspace-ID": "workspace-a"}
    )
    thread_id = response1.json()["threadId"]

    # Continue with workspace B in session
    response2 = await client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "Continue"}],
            "threadId": thread_id
        },
        headers={"X-Workspace-ID": "workspace-b"}
    )

    # Should use workspace-a from thread
    # Verify via cache key generation or tool routing


@pytest.mark.asyncio
async def test_unique_client_message_id(client: AsyncClient):
    """Test client message ID uniqueness per thread."""
    request = {
        "messages": [{"role": "user", "content": "Test message"}],
        "clientMessageId": "unique-123"
    }

    # First request creates thread and message
    response1 = await client.post("/chat", json=request)
    thread_id = response1.json()["threadId"]

    # Second request with same clientMessageId to same thread
    request["threadId"] = thread_id
    response2 = await client.post("/chat", json=request)

    # Should get same response (idempotent)
    assert response1.json()["reply"] == response2.json()["reply"]

    # Verify only one message in DB (would need DB access)
```

### 5.2 Manual Testing Checklist

```bash
# 1. Basic thread creation
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "messages": [{"role": "user", "content": "Start a new conversation"}],
    "returnShareToken": true
  }'
# Save threadId and shareToken from response

# 2. Continue thread by ID
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "messages": [{"role": "user", "content": "Continue our conversation"}],
    "threadId": "YOUR_THREAD_ID"
  }'

# 3. Access via share token (simulating different device)
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Accessing from phone"}],
    "shareToken": "thr_YOUR_TOKEN"
  }'

# 4. Test idempotency
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "messages": [{"role": "user", "content": "Duplicate test"}],
    "clientMessageId": "test-123"
  }'
# Run same command again - should not create duplicate

# 5. Verify thread history in database
psql $DATABASE_URL -c "
  SELECT tm.role, tm.content->>'text' as message, tm.created_at
  FROM thread_messages tm
  WHERE thread_id = 'YOUR_THREAD_ID'
  ORDER BY created_at;
"
```

---

## Implementation Timeline

### Day 1 (6 hours)

- **Hour 1-2**: Database migration and models
- **Hour 3-4**: Thread service implementation
- **Hour 5-6**: Chat endpoint enhancement

### Day 2 (4 hours)

- **Hour 1-2**: Tool call journaling
- **Hour 3-4**: Testing and debugging

### Rollout Strategy

1. **Deploy database changes** (no impact)
2. **Deploy code with feature flag** (disabled by default)
3. **Test with small group** (enable via header)
4. **Gradual rollout** (increase percentage)
5. **Full deployment** (remove feature flag)

---

## Configuration

### Environment Variables

```bash
# .env additions
ENABLE_THREADS=true
THREAD_MESSAGE_LIMIT=100
SHARE_TOKEN_TTL_HOURS=168  # 1 week
DEFAULT_USER_ID=00000000-0000-0000-0000-000000000000
```

### Feature Flags

```python
# src/config.py additions
class Settings(BaseSettings):
    # Thread configuration
    enable_threads: bool = Field(True, description="Enable thread support")
    thread_message_limit: int = Field(100, description="Max messages per thread")
    share_token_ttl_hours: int = Field(168, description="Share token validity")
    default_user_id: str = Field(
        "00000000-0000-0000-0000-000000000000",
        description="Default user for MVP"
    )
```

---

## Monitoring & Observability

### Key Metrics

```python
# Metrics to track
thread_metrics = {
    "threads_created": Counter("threads_created_total"),
    "messages_saved": Counter("messages_saved_total"),
    "share_tokens_generated": Counter("share_tokens_generated_total"),
    "tool_calls_journaled": Counter("tool_calls_journaled_total"),
    "partial_failures": Counter("partial_failures_total"),
    "thread_resume_success": Counter("thread_resume_success_total"),
    "thread_resume_failure": Counter("thread_resume_failure_total"),
}

# Log important events
logger.info("thread_lifecycle", {
    "event": "thread_created|message_saved|token_generated",
    "thread_id": str(thread_id),
    "user_id": user_id,
    "workspace_id": workspace_id,
    "message_count": count
})
```

### Database Queries for Monitoring

```sql
-- Active threads by day
SELECT DATE(created_at), COUNT(*)
FROM threads
WHERE deleted_at IS NULL
GROUP BY DATE(created_at);

-- Message velocity
SELECT DATE(created_at), COUNT(*), AVG(tokens_input + tokens_output)
FROM thread_messages
GROUP BY DATE(created_at);

-- Tool call success rate
SELECT
  tool_name,
  COUNT(*) as total_calls,
  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful,
  AVG(EXTRACT(EPOCH FROM (finished_at - started_at))) as avg_duration_seconds
FROM tool_call_log
GROUP BY tool_name;

-- Partial failures
SELECT DATE(created_at), COUNT(*)
FROM thread_messages
WHERE status = 'error' AND tool_calls IS NOT NULL
GROUP BY DATE(created_at);
```

---

## Troubleshooting Guide

### Common Issues

1. **Thread not found**
   - Check thread ID format (UUID)
   - Verify thread not soft-deleted
   - Check share token format (thr_prefix)

2. **Duplicate messages**
   - Verify client_message_id is unique
   - Check idempotency key generation

3. **Tool calls not journaled**
   - Verify hook is set in orchestrator
   - Check database connection in hook
   - Verify immediate commit after journaling

4. **Cache not invalidated**
   - Check cache key format
   - Verify invalidation after tool success
   - Check cache service connectivity

5. **Share token not working**
   - Verify token format
   - Check hash calculation
   - Verify token not expired

---

## Security Considerations

1. **Share Tokens**
   - Use cryptographically secure random generation
   - Store only hash in database
   - Consider expiration for long-lived tokens

2. **User Isolation**
   - DEFAULT_USER for MVP is a security risk
   - Must implement proper auth before production
   - Consider workspace isolation

3. **Message Content**
   - Store as JSONB for flexibility
   - Consider encryption for sensitive data
   - Implement content filtering if needed

4. **Rate Limiting**
   - Implement per-thread rate limits
   - Monitor for abuse patterns
   - Consider message count limits

---

## Future Enhancements (Post-MVP)

1. **Performance**
   - Redis cache for recent messages
   - Message pagination
   - Background thread cleanup

2. **Features**
   - Thread search
   - Message editing
   - Thread forking
   - Export/import

3. **Security**
   - Real user authentication
   - Granular permissions
   - Audit logging

4. **Scalability**
   - Thread sharding
   - Read replicas
   - Message archival
