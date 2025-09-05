"""
Device session service for managing device-based sessions.

This module provides functionality for managing device sessions which track
devices accessing the API and their associated workspace context.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import DeviceSession
from src.utils.logging import get_logger

logger = get_logger(__name__)


class DeviceSessionService:
    """Service for managing device sessions."""

    @staticmethod
    async def get_or_create_device_session(
        db: AsyncSession,
        device_id: str,
        device_name: Optional[str] = None,
        user_id: Optional[UUID] = None,
        workspace_id: Optional[str] = None,
    ) -> DeviceSession:
        """
        Get existing device session or create a new one.

        Args:
            db: Database session
            device_id: Unique device identifier
            device_name: Optional device name/description
            user_id: Optional user ID
            workspace_id: Optional workspace ID

        Returns:
            DeviceSession instance
        """
        # Try to find existing device session
        result = await db.execute(
            select(DeviceSession).where(DeviceSession.device_id == device_id)
        )
        device_session = result.scalar_one_or_none()

        if device_session:
            # Update last seen timestamp
            device_session.last_seen_at = datetime.now(timezone.utc)

            # Update workspace if provided
            if workspace_id and workspace_id != device_session.workspace_id:
                device_session.workspace_id = workspace_id
                logger.info(
                    "Updated device session workspace",
                    device_id=device_id,
                    old_workspace=device_session.workspace_id,
                    new_workspace=workspace_id,
                )

            # Update user if provided
            if user_id and user_id != device_session.user_id:
                device_session.user_id = user_id
                logger.info(
                    "Updated device session user",
                    device_id=device_id,
                    old_user=device_session.user_id,
                    new_user=user_id,
                )
        else:
            # Create new device session
            device_session = DeviceSession(
                device_id=device_id,
                device_name=device_name,
                user_id=user_id,
                workspace_id=workspace_id,
                last_seen_at=datetime.now(timezone.utc),
            )
            db.add(device_session)
            logger.info(
                "Created new device session",
                device_id=device_id,
                user_id=user_id,
                workspace_id=workspace_id,
            )

        await db.commit()
        await db.refresh(device_session)
        return device_session

    @staticmethod
    async def get_device_session(
        db: AsyncSession, device_id: str
    ) -> Optional[DeviceSession]:
        """
        Get device session by device ID.

        Args:
            db: Database session
            device_id: Device identifier

        Returns:
            DeviceSession if found, None otherwise
        """
        result = await db.execute(
            select(DeviceSession).where(DeviceSession.device_id == device_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def cleanup_old_sessions(db: AsyncSession, days_inactive: int = 90) -> int:
        """
        Clean up device sessions that haven't been seen in specified days.

        Args:
            db: Database session
            days_inactive: Number of days of inactivity before cleanup

        Returns:
            Number of sessions deleted
        """
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_inactive)

        # Find old sessions
        result = await db.execute(
            select(DeviceSession).where(DeviceSession.last_seen_at < cutoff_date)
        )
        old_sessions = result.scalars().all()

        # Delete old sessions
        count = 0
        for session in old_sessions:
            await db.delete(session)
            count += 1

        if count > 0:
            await db.commit()
            logger.info(f"Cleaned up {count} old device sessions")

        return count
