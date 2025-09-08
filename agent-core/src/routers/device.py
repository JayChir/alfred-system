"""
Device session management endpoints.

Provides endpoints for creating and managing device sessions
for transport continuity and token metering.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings, get_settings
from src.db.database import get_async_session as get_db
from src.middleware.device_session import RequiredDeviceSession
from src.services.device_session_service import DeviceSessionService
from src.utils.logging import get_logger

# Create router for device endpoints
router = APIRouter()
logger = get_logger(__name__)


class CreateDeviceSessionRequest(BaseModel):
    """Request model for creating a new device session."""

    userId: str = Field(
        ...,
        description="User ID to create session for",
        json_schema_extra={"example": "123e4567-e89b-12d3-a456-426614174000"},
    )
    workspaceId: Optional[str] = Field(
        None,
        description="Optional workspace ID for MCP routing",
        json_schema_extra={"example": "workspace_123"},
    )


class CreateDeviceSessionResponse(BaseModel):
    """Response model for device session creation."""

    deviceToken: str = Field(
        ...,
        description="Device token for authentication (dtok_...)",
        json_schema_extra={"example": "dtok_secure-token-here"},
    )
    expiresAt: str = Field(
        ...,
        description="Session expiry timestamp (ISO format)",
        json_schema_extra={"example": "2024-01-20T12:00:00Z"},
    )
    sessionId: str = Field(
        ...,
        description="Session ID for reference",
        json_schema_extra={"example": "123e4567-e89b-12d3-a456-426614174000"},
    )


@router.post(
    "/device/session",
    response_model=CreateDeviceSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new device session",
    description="Create a new device session for token-based authentication and metering",
)
async def create_device_session(
    request: CreateDeviceSessionRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CreateDeviceSessionResponse:
    """
    Create a new device session for authentication and token metering.

    This endpoint creates a new device session with:
    - Secure token generation (256-bit entropy)
    - 7-day sliding expiry with 30-day hard cap
    - Optional workspace binding for MCP routing
    - Token usage tracking capabilities

    The returned token should be:
    - Stored securely on the device
    - Sent in Authorization header as "Bearer dtok_..."
    - Or sent in cookie as "dtok=..."
    - Or included in request body as "deviceToken"

    Args:
        request: Device session creation request
        response: FastAPI response for cookie setting
        db: Database session

    Returns:
        CreateDeviceSessionResponse with token and metadata

    Raises:
        HTTPException: 400 if invalid user ID format
    """
    # Initialize service
    device_session_service = DeviceSessionService()

    # Parse user ID
    try:
        user_id = UUID(request.userId)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID format - must be UUID",
        ) from None

    # Create device session
    try:
        token = await device_session_service.create_device_session(
            db,
            user_id=user_id,
            workspace_id=request.workspaceId,
        )

        # Get session details for response
        session_context = await device_session_service.validate_device_token(db, token)

        if not session_context:
            # Should not happen, but handle gracefully
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to validate newly created session",
            )

        # Set cookie for browser-based clients (optional)
        # Cookie is HttpOnly for security, with 7-day expiry
        # Use secure flag in production for HTTPS-only transmission
        response.set_cookie(
            key="dtok",
            value=token,
            httponly=True,
            samesite="lax",
            max_age=7 * 24 * 3600,  # 7 days in seconds
            secure=settings.app_env == "production",  # Secure in production only
        )

        logger.info(
            "Created device session",
            user_id=str(user_id),
            session_id=str(session_context.session_id),
            workspace_id=request.workspaceId,
        )

        return CreateDeviceSessionResponse(
            deviceToken=token,
            expiresAt=session_context.expires_at.isoformat(),
            sessionId=str(session_context.session_id),
        )

    except Exception as e:
        logger.error(
            "Failed to create device session",
            user_id=str(user_id),
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create device session",
        ) from None


@router.delete(
    "/device/session",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke current device session",
    description="Revoke the current device session (logout)",
)
async def revoke_device_session(
    device_session: RequiredDeviceSession,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Revoke the current device session.

    This endpoint revokes (soft deletes) the current device session,
    effectively logging out the device. The session will no longer
    be valid for authentication.

    Args:
        device_session: Current device session (required)
        response: FastAPI response for cookie clearing
        db: Database session

    Raises:
        HTTPException: 401 if no valid device session
    """
    # Initialize service
    device_session_service = DeviceSessionService()

    # Revoke the session
    success = await device_session_service.revoke_device_session(
        db, device_session.session_id
    )

    if success:
        # Clear cookie if present
        response.delete_cookie(key="dtok")

        logger.info(
            "Revoked device session",
            session_id=str(device_session.session_id),
            user_id=str(device_session.user_id),
        )
    else:
        logger.warning(
            "Failed to revoke device session - not found",
            session_id=str(device_session.session_id),
        )


@router.get(
    "/device/session",
    response_model=dict,
    summary="Get current device session info",
    description="Get information about the current device session",
)
async def get_device_session_info(
    device_session: RequiredDeviceSession,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Get information about the current device session.

    Returns session metadata including expiry, usage stats,
    and workspace binding.

    Args:
        device_session: Current device session (required)
        db: Database session

    Returns:
        Dictionary with session information

    Raises:
        HTTPException: 401 if no valid device session
    """
    # Initialize service
    device_session_service = DeviceSessionService()

    # Get session stats
    stats = await device_session_service.get_session_stats(
        db, device_session.session_id
    )

    if not stats:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return stats
