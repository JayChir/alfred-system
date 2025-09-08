"""
Type definitions and data classes for Alfred Agent Core.

This module contains shared type definitions, data classes, and type aliases
used across the application for better type safety and code organization.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID


@dataclass
class DeviceSessionContext:
    """
    Context object for device session dependency injection.

    This dataclass is returned from device session validation and contains
    all the information needed for request processing, workspace routing,
    and usage tracking.

    Attributes:
        session_id: Unique session identifier for database operations
        user_id: User who owns this session
        workspace_id: Active workspace for MCP routing (optional)
        expires_at: Current session expiry timestamp
        tokens_remaining: Reserved for future rate limiting (optional)
    """

    session_id: UUID
    user_id: UUID
    workspace_id: Optional[str]
    expires_at: datetime
    tokens_remaining: Optional[int] = None  # For future rate limiting features


@dataclass
class TokenUsage:
    """
    Token usage information for billing and rate limiting.

    Tracks input and output tokens consumed during request processing.
    Used for metering, billing calculations, and usage analytics.

    Attributes:
        input: Number of input tokens consumed
        output: Number of output tokens generated
    """

    input: int
    output: int

    @property
    def total(self) -> int:
        """Total tokens consumed (input + output)."""
        return self.input + self.output


@dataclass
class WorkspaceContext:
    """
    Workspace resolution context for MCP routing.

    Contains information about which workspace should be used for
    MCP tool routing, with clear precedence rules.

    Attributes:
        thread_workspace: Workspace from thread context (highest precedence)
        device_workspace: Workspace from device session (fallback)
        effective_workspace: Computed final workspace to use
    """

    thread_workspace: Optional[str]
    device_workspace: Optional[str]

    @property
    def effective_workspace(self) -> Optional[str]:
        """
        Compute effective workspace with thread precedence.

        Thread workspace always wins if present, device workspace
        is fallback, None means workspace-agnostic mode.
        """
        return self.thread_workspace or self.device_workspace


# Type aliases for better code readability
DeviceToken = str  # Raw device token (dtok_xxx)
TokenHash = bytes  # SHA-256 hash of device token
WorkspaceId = str  # Workspace identifier (usually Notion workspace ID)
RequestId = str  # Unique request identifier for tracing
