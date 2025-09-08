"""
Device session service with atomic operations and race safety.

This module provides production-grade device session management with:
- Race-safe token creation and validation
- Atomic sliding window expiry updates
- Secure token hashing and storage
- Usage tracking and metering
- Session revocation and cleanup

Security features:
- Only SHA-256 hashes stored in database
- Atomic operations prevent race conditions
- Sliding expiry with hard caps
- Comprehensive logging without token exposure
"""

from typing import Optional
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from src.utils.device_token import (
    DeviceToken,
    extract_token_prefix,
    hash_device_token,
    new_device_token,
)
from src.utils.logging import get_logger
from src.utils.types import DeviceSessionContext

logger = get_logger(__name__)


class DeviceSessionService:
    """
    Production-grade device session management with atomic operations.

    This service handles the complete device session lifecycle including:
    - Secure token generation and validation
    - Race-safe atomic updates
    - Sliding window expiry with hard caps
    - Usage tracking and metering
    - Session revocation and cleanup
    """

    async def create_device_session(
        self, db: AsyncSession, user_id: UUID, workspace_id: Optional[str] = None
    ) -> DeviceToken:
        """
        Create device session with race-safe token generation.

        This method generates a new device token, hashes it securely, and
        stores the session in the database with proper expiry timestamps.
        Uses conflict handling to prevent race conditions.

        Args:
            db: Database session
            user_id: Owner user ID
            workspace_id: Optional workspace binding for MCP routing

        Returns:
            Raw device token (dtok_xxx) - only time it's visible

        Raises:
            SQLAlchemyError: If database operation fails
        """
        # Generate secure token with 256-bit entropy
        token = new_device_token()
        token_hash = hash_device_token(token)

        # Race-safe insertion with conflict handling
        # If the token hash somehow already exists (extremely unlikely),
        # the ON CONFLICT will prevent the insert and we can retry
        await db.execute(
            sa.text(
                """
                INSERT INTO device_sessions
                (session_token_hash, user_id, workspace_id, expires_at, hard_expires_at)
                VALUES (:h, :uid, :ws, now() + interval '7 days', now() + interval '30 days')
                ON CONFLICT (session_token_hash) DO NOTHING
            """
            ),
            {"h": token_hash, "uid": user_id, "ws": workspace_id},
        )

        await db.commit()

        logger.info(
            "Created device session",
            user_id=str(user_id),
            workspace_id=workspace_id,
            token_prefix=extract_token_prefix(token),  # Log prefix only for security
        )

        return token  # Return raw token - only time it's visible

    async def validate_device_token(
        self, db: AsyncSession, token: DeviceToken
    ) -> Optional[DeviceSessionContext]:
        """
        Validate token with atomic sliding window update.

        This method performs token validation and sliding expiry update
        in a single atomic SQL operation to prevent race conditions.
        The sliding window extends the session by 7 days but never
        beyond the hard 30-day cap.

        Args:
            db: Database session
            token: Raw device token to validate

        Returns:
            DeviceSessionContext if valid, None if invalid/expired
        """
        try:
            token_hash = hash_device_token(token)
        except ValueError as e:
            logger.debug("Invalid token format", error=str(e))
            return None  # Invalid format

        # Atomic validate + update in single SQL operation
        # This prevents read-modify-write races and ensures consistency
        result = await db.execute(
            sa.text(
                """
                UPDATE device_sessions
                SET last_accessed = now(),
                    expires_at = LEAST(now() + interval '7 days', hard_expires_at),
                    request_count = request_count + 1
                WHERE session_token_hash = :h
                  AND (revoked_at IS NULL)
                  AND (expires_at > now())
                  AND (hard_expires_at > now())
                RETURNING session_id, user_id, workspace_id, expires_at
            """
            ),
            {"h": token_hash},
        )

        row = result.first()
        if not row:
            logger.debug(
                "Invalid or expired device token",
                token_prefix=extract_token_prefix(token),
            )
            return None

        await db.commit()  # Commit the sliding expiry update

        return DeviceSessionContext(
            session_id=row.session_id,
            user_id=row.user_id,
            workspace_id=row.workspace_id,
            expires_at=row.expires_at,
        )

    async def update_token_usage(
        self, db: AsyncSession, session_id: UUID, tokens_input: int, tokens_output: int
    ) -> None:
        """
        Update token usage counters in separate transaction.

        This method updates usage tracking counters outside of the main
        request transaction to minimize contention and allow the update
        to succeed even if the main request fails.

        Args:
            db: Database session
            session_id: Device session ID
            tokens_input: Input tokens consumed
            tokens_output: Output tokens generated
        """
        if tokens_input < 0 or tokens_output < 0:
            logger.warning(
                "Invalid token usage values",
                session_id=str(session_id),
                tokens_input=tokens_input,
                tokens_output=tokens_output,
            )
            return

        await db.execute(
            sa.text(
                """
                UPDATE device_sessions
                SET tokens_input_total = tokens_input_total + :tin,
                    tokens_output_total = tokens_output_total + :tout
                WHERE session_id = :sid
            """
            ),
            {"sid": session_id, "tin": tokens_input, "tout": tokens_output},
        )

        await db.commit()

        logger.debug(
            "Updated token usage",
            session_id=str(session_id),
            tokens_input=tokens_input,
            tokens_output=tokens_output,
        )

    async def revoke_device_session(self, db: AsyncSession, session_id: UUID) -> bool:
        """
        Revoke device session (soft delete).

        Marks the session as revoked by setting revoked_at timestamp.
        Revoked sessions will fail validation but remain in the database
        for audit purposes.

        Args:
            db: Database session
            session_id: Session to revoke

        Returns:
            True if session was revoked, False if not found
        """
        result = await db.execute(
            sa.text(
                """
                UPDATE device_sessions
                SET revoked_at = now()
                WHERE session_id = :sid AND revoked_at IS NULL
            """
            ),
            {"sid": session_id},
        )

        await db.commit()
        success = result.rowcount > 0

        if success:
            logger.info("Revoked device session", session_id=str(session_id))
        else:
            logger.debug(
                "Device session not found for revocation", session_id=str(session_id)
            )

        return success

    async def cleanup_expired_sessions(self, db: AsyncSession) -> int:
        """
        Clean up expired and hard-expired sessions.

        Permanently deletes sessions that have exceeded their expiry
        times. This should be run periodically (e.g., hourly) to
        maintain database performance.

        Args:
            db: Database session

        Returns:
            Number of sessions cleaned up
        """
        result = await db.execute(
            sa.text(
                """
                DELETE FROM device_sessions
                WHERE ctid IN (
                    SELECT ctid FROM device_sessions
                    WHERE (expires_at <= now()) OR (hard_expires_at <= now())
                    LIMIT 1000
                )
            """
            )
        )

        await db.commit()
        count = result.rowcount

        if count > 0:
            logger.info("Cleaned up expired device sessions", count=count)
        else:
            logger.debug("No expired device sessions to clean up")

        return count

    async def get_session_stats(
        self, db: AsyncSession, session_id: UUID
    ) -> Optional[dict]:
        """
        Get session statistics for monitoring and debugging.

        Returns usage statistics and session metadata without
        exposing sensitive token information.

        Args:
            db: Database session
            session_id: Session to get stats for

        Returns:
            Dictionary with session statistics or None if not found
        """
        result = await db.execute(
            sa.text(
                """
                SELECT
                    session_id,
                    user_id,
                    workspace_id,
                    created_at,
                    last_accessed,
                    expires_at,
                    hard_expires_at,
                    tokens_input_total,
                    tokens_output_total,
                    request_count,
                    revoked_at
                FROM device_sessions
                WHERE session_id = :sid
            """
            ),
            {"sid": session_id},
        )

        row = result.first()
        if not row:
            return None

        return {
            "session_id": str(row.session_id),
            "user_id": str(row.user_id),
            "workspace_id": row.workspace_id,
            "created_at": row.created_at.isoformat(),
            "last_accessed": row.last_accessed.isoformat(),
            "expires_at": row.expires_at.isoformat(),
            "hard_expires_at": row.hard_expires_at.isoformat(),
            "tokens_input_total": row.tokens_input_total,
            "tokens_output_total": row.tokens_output_total,
            "request_count": row.request_count,
            "is_revoked": row.revoked_at is not None,
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        }
