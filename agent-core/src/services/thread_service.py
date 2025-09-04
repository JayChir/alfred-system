"""
Thread management service for conversation continuity.

Handles thread creation, message persistence, and cross-device access.
Provides idempotency guarantees and partial failure recovery through tool call journaling.
"""

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from src.config import get_settings
from src.db.models import Thread, ThreadMessage, ToolCallLog
from src.utils.crypto import CryptoService
from src.utils.logging import get_logger

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

    def __init__(self, crypto_service: Optional[CryptoService] = None):
        """
        Initialize thread service.

        Args:
            crypto_service: Optional crypto service for token operations.
                           If not provided, creates one from settings.
        """
        if crypto_service is None:
            settings = get_settings()
            self.crypto = CryptoService(settings.fernet_key)
        else:
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
                and_(Thread.id == thread_id, Thread.deleted_at.is_(None))
            )
            result = await db.execute(stmt)
            thread = result.scalar_one_or_none()

            if thread:
                logger.info(
                    "Found thread by ID",
                    thread_id=thread_id,
                    has_messages=bool(thread.messages) if thread.messages else False,
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
                        Thread.share_token_expires_at > func.now(),
                    ),
                )
            )
            result = await db.execute(stmt)
            thread = result.scalar_one_or_none()

            if thread:
                logger.info(
                    "Found thread by share token",
                    thread_id=str(thread.id),
                    has_token=True,
                )

        # Create new thread if not found
        if not thread:
            settings = get_settings()
            # Use provided user_id or fall back to default from settings
            default_user_id = (
                UUID(settings.default_user_id) if settings.default_user_id else None
            )
            thread = Thread(
                owner_user_id=UUID(user_id) if user_id else default_user_id,
                workspace_id=workspace_id,
                last_activity_at=datetime.utcnow(),
            )
            db.add(thread)
            await db.flush()  # Get the ID

            logger.info(
                "Created new thread",
                thread_id=str(thread.id),
                workspace_id=workspace_id,
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
                    ThreadMessage.client_message_id == client_message_id,
                )
            )
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                logger.info(
                    "Duplicate message detected",
                    thread_id=str(thread.id),
                    client_message_id=client_message_id,
                    message_id=str(existing.id),
                )
                return existing

        # Normalize content to ensure it's always a dict
        if isinstance(content, str):
            content = {"text": content}
        elif not isinstance(content, dict):
            content = {"text": str(content)}

        # Create new message
        message = ThreadMessage(
            thread_id=thread.id,
            role=role,
            content=content,
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
            has_tools=bool(tool_calls),
        )

        return message

    async def get_thread_messages(
        self,
        db: AsyncSession,
        thread_id: UUID,
        limit: int = 100,
        include_tool_calls: bool = True,
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
        stmt = (
            select(ThreadMessage)
            .where(ThreadMessage.thread_id == thread_id)
            .order_by(ThreadMessage.created_at.asc())
            .limit(limit)
        )

        result = await db.execute(stmt)
        messages = result.scalars().all()

        logger.debug(
            "Retrieved thread messages",
            thread_id=str(thread_id),
            count=len(messages),
            limit=limit,
        )

        return list(messages)

    async def generate_share_token(
        self, db: AsyncSession, thread: Thread, ttl_hours: Optional[int] = None
    ) -> str:
        """
        Generate share token for cross-device access with expiry.

        Args:
            db: Database session
            thread: Thread to generate token for
            ttl_hours: Token TTL in hours (uses settings default if not provided)

        Returns:
            Share token string (thr_<base64url>)
        """
        settings = get_settings()
        if ttl_hours is None:
            ttl_hours = settings.share_token_ttl_hours

        # Generate secure random token
        token_bytes = secrets.token_urlsafe(32)
        token = f"thr_{token_bytes}"

        # Store hash in database with expiry
        token_hash = hashlib.sha256(token.encode()).digest()
        thread.share_token_hash = token_hash
        thread.share_token_expires_at = datetime.utcnow() + timedelta(hours=ttl_hours)

        await db.flush()

        logger.info(
            "Generated share token",
            thread_id=str(thread.id),
            token_prefix=token[:12],
            expires_hours=ttl_hours,
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
        idempotency_key: Optional[str] = None,
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
            ToolCallLog entry (existing if duplicate)
        """
        # Generate stable idempotency key if not provided
        if not idempotency_key:
            # Canonical JSON for deterministic serialization
            # This ensures the same args always produce the same key
            canonical_args = json.dumps(args, separators=(",", ":"), sort_keys=True)
            key_data = f"{request_id}:{thread_id}:{user_message_id}:{tool_name}:{canonical_args}:{call_index}"
            idempotency_key = hashlib.sha256(key_data.encode()).hexdigest()

        # Check for existing call with this idempotency key
        stmt = select(ToolCallLog).where(ToolCallLog.idempotency_key == idempotency_key)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            logger.info(
                "Tool call already executed",
                tool_name=tool_name,
                status=existing.status,
                idempotency_key=idempotency_key[:16],  # Log partial key for debugging
            )
            return existing

        # Create new log entry
        log_entry = ToolCallLog(
            request_id=UUID(request_id) if isinstance(request_id, str) else request_id,
            thread_id=thread_id,
            message_id=message_id,
            call_index=call_index,
            idempotency_key=idempotency_key,
            tool_name=tool_name,
            args=args,
            status="pending",
        )

        db.add(log_entry)
        await db.flush()

        logger.info(
            "Logged tool call",
            tool_name=tool_name,
            thread_id=str(thread_id),
            call_index=call_index,
            log_id=str(log_entry.id),
        )

        return log_entry

    async def update_tool_call_status(
        self,
        db: AsyncSession,
        log_entry: ToolCallLog,
        status: str,
        result_digest: Optional[str] = None,
        error: Optional[str] = None,
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
            # Truncate long errors to fit in database
            log_entry.error = error[:1000]

        await db.flush()

        # Calculate duration for logging
        duration_ms = None
        if log_entry.finished_at and log_entry.started_at:
            duration_ms = (
                log_entry.finished_at - log_entry.started_at
            ).total_seconds() * 1000

        logger.info(
            "Updated tool call status",
            tool_name=log_entry.tool_name,
            status=status,
            duration_ms=duration_ms,
            has_error=bool(error),
        )

    async def get_recent_tool_calls(
        self,
        db: AsyncSession,
        thread_id: UUID,
        limit: int = 50,
        status: Optional[str] = None,
    ) -> List[ToolCallLog]:
        """
        Get recent tool calls for a thread.

        Useful for debugging and recovery from partial failures.

        Args:
            db: Database session
            thread_id: Thread ID
            limit: Maximum entries to return
            status: Optional status filter (pending/success/failed)

        Returns:
            List of tool call log entries
        """
        stmt = (
            select(ToolCallLog)
            .where(ToolCallLog.thread_id == thread_id)
            .order_by(ToolCallLog.started_at.desc())
            .limit(limit)
        )

        if status:
            stmt = stmt.where(ToolCallLog.status == status)

        result = await db.execute(stmt)
        entries = result.scalars().all()

        logger.debug(
            "Retrieved tool call logs",
            thread_id=str(thread_id),
            count=len(entries),
            status_filter=status,
        )

        return list(entries)

    async def cleanup_expired_tokens(self, db: AsyncSession) -> int:
        """
        Clean up expired share tokens.

        This should be called periodically (e.g., daily) to clean up expired tokens.

        Args:
            db: Database session

        Returns:
            Number of tokens cleaned up
        """
        # Find threads with expired share tokens
        stmt = select(Thread).where(
            and_(
                Thread.share_token_hash.is_not(None),
                Thread.share_token_expires_at.is_not(None),
                Thread.share_token_expires_at < func.now(),
            )
        )

        result = await db.execute(stmt)
        threads = result.scalars().all()

        # Clear expired tokens
        for thread in threads:
            thread.share_token_hash = None
            thread.share_token_expires_at = None

        await db.flush()

        if threads:
            logger.info(
                "Cleaned up expired share tokens",
                count=len(threads),
                thread_ids=[str(t.id) for t in threads],
            )

        return len(threads)

    async def soft_delete_thread(
        self, db: AsyncSession, thread_id: UUID
    ) -> Optional[Thread]:
        """
        Soft delete a thread (mark as deleted).

        Args:
            db: Database session
            thread_id: Thread ID to delete

        Returns:
            Thread object if found and deleted, None otherwise
        """
        stmt = select(Thread).where(
            and_(Thread.id == thread_id, Thread.deleted_at.is_(None))
        )
        result = await db.execute(stmt)
        thread = result.scalar_one_or_none()

        if thread:
            thread.deleted_at = datetime.utcnow()
            await db.flush()

            logger.info("Soft deleted thread", thread_id=str(thread_id))

        return thread
