"""
Workspace resolution service for MCP tool routing.

This module handles workspace precedence resolution for MCP tool routing:
- Thread workspace (highest precedence)
- Device session workspace (fallback)
- Default workspace-agnostic mode (None)

The resolver integrates with both thread and device session systems
to provide consistent workspace context for MCP tool calls.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.thread_service import ThreadService
from src.utils.logging import get_logger
from src.utils.types import WorkspaceContext

logger = get_logger(__name__)


class WorkspaceResolver:
    """
    Service for resolving workspace context with proper precedence.

    This service implements the workspace precedence hierarchy:
    1. Thread workspace (from thread metadata - highest precedence)
    2. Device workspace (from device session - fallback)
    3. None (workspace-agnostic mode - lowest precedence)

    The resolved workspace is used for MCP tool routing to ensure
    tools are called within the correct workspace context.
    """

    def __init__(self, thread_service: ThreadService):
        """
        Initialize workspace resolver with thread service dependency.

        Args:
            thread_service: Thread service for workspace metadata lookup
        """
        self.thread_service = thread_service

    async def resolve_workspace(
        self,
        db: AsyncSession,
        thread_id: Optional[UUID] = None,
        device_workspace: Optional[str] = None,
    ) -> WorkspaceContext:
        """
        Resolve workspace with proper precedence hierarchy.

        This method implements the core workspace resolution logic:
        1. If thread_id provided, check thread metadata for workspace
        2. Use device_workspace as fallback if available
        3. Return None for workspace-agnostic mode

        Args:
            db: Database session for thread metadata lookup
            thread_id: Optional thread ID to check for workspace override
            device_workspace: Optional workspace from device session

        Returns:
            WorkspaceContext with resolved workspace information
        """
        thread_workspace = None

        # Check for thread workspace override (highest precedence)
        if thread_id:
            try:
                # Query thread directly to get workspace_id
                from src.db.models import Thread

                result = await db.execute(
                    select(Thread.workspace_id).where(
                        Thread.id == thread_id, Thread.deleted_at.is_(None)
                    )
                )
                row = result.scalar_one_or_none()
                if row:
                    thread_workspace = row
                    logger.debug(
                        "Found thread workspace override",
                        thread_id=str(thread_id),
                        thread_workspace=thread_workspace,
                    )
            except Exception as e:
                logger.warning(
                    "Failed to resolve thread workspace, using device fallback",
                    thread_id=str(thread_id),
                    error=str(e),
                )

        # Create workspace context with precedence resolution
        workspace_context = WorkspaceContext(
            thread_workspace=thread_workspace, device_workspace=device_workspace
        )

        logger.debug(
            "Resolved workspace context",
            thread_workspace=thread_workspace,
            device_workspace=device_workspace,
            effective_workspace=workspace_context.effective_workspace,
        )

        return workspace_context

    async def override_thread_workspace(
        self,
        db: AsyncSession,
        thread_id: UUID,
        new_workspace: Optional[str],
    ) -> bool:
        """
        Update thread workspace metadata for future resolution.

        This method allows changing the workspace associated with a thread,
        which will take precedence over device session workspace in future
        requests within this thread context.

        Args:
            db: Database session for thread metadata update
            thread_id: Thread to update workspace for
            new_workspace: New workspace ID or None to clear

        Returns:
            True if update succeeded, False if thread not found
        """
        try:
            # Update thread workspace directly in database
            from src.db.models import Thread

            result = await db.execute(
                update(Thread)
                .where(Thread.id == thread_id, Thread.deleted_at.is_(None))
                .values(workspace_id=new_workspace)
            )

            await db.commit()
            success = result.rowcount > 0

            if success:
                logger.info(
                    "Updated thread workspace",
                    thread_id=str(thread_id),
                    new_workspace=new_workspace,
                )
            else:
                logger.warning(
                    "Failed to update thread workspace - thread not found",
                    thread_id=str(thread_id),
                )

            return success

        except Exception as e:
            logger.error(
                "Error updating thread workspace",
                thread_id=str(thread_id),
                new_workspace=new_workspace,
                error=str(e),
            )
            await db.rollback()
            return False

    def get_effective_workspace(
        self, workspace_context: WorkspaceContext
    ) -> Optional[str]:
        """
        Extract effective workspace from context for convenience.

        This is a helper method that delegates to the WorkspaceContext
        effective_workspace property for cleaner API usage.

        Args:
            workspace_context: Workspace context from resolve_workspace()

        Returns:
            Effective workspace ID or None for workspace-agnostic mode
        """
        return workspace_context.effective_workspace
