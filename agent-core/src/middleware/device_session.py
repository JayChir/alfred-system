"""
Device session middleware for FastAPI dependency injection.

This module provides FastAPI middleware and dependencies for device session
management, enabling automatic session validation and context injection
for authenticated endpoints.

Key features:
- Optional device token extraction from headers/cookies
- Automatic session validation and renewal
- Context injection via dependency injection
- Graceful handling of missing/invalid tokens
"""

from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.database import get_db
from src.services.device_session_service import DeviceSessionService
from src.utils.device_token import validate_token_format
from src.utils.logging import get_logger
from src.utils.types import DeviceSessionContext, DeviceToken

logger = get_logger(__name__)


class DeviceSessionDependency:
    """
    FastAPI dependency class for device session management.

    This class provides dependency injection for device session validation
    and context extraction. It handles the complete session lifecycle:
    - Token extraction from multiple sources (header, cookie, body)
    - Format validation and security checks
    - Database session validation with sliding expiry
    - Context object creation for downstream services
    """

    def __init__(self):
        """Initialize device session dependency with service instance."""
        self.device_session_service = DeviceSessionService()

    async def extract_device_token(
        self,
        request: Request,
        authorization: Annotated[Optional[str], Header()] = None,
    ) -> Optional[DeviceToken]:
        """
        Extract device token from multiple sources.

        Checks for device token in the following order:
        1. Authorization header (Bearer dtok_...)
        2. Cookie (dtok=...)
        3. Request body (deviceToken field) - for non-GET requests

        Args:
            request: FastAPI request object
            authorization: Authorization header value

        Returns:
            Device token if found and valid format, None otherwise
        """
        # 1. Check Authorization header (Bearer dtok_...)
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:]  # Remove "Bearer " prefix
            if validate_token_format(token):
                logger.debug("Device token extracted from Authorization header")
                return token

        # 2. Check Cookie (dtok=...)
        cookie_token = request.cookies.get("dtok")
        if cookie_token and validate_token_format(cookie_token):
            logger.debug("Device token extracted from cookie")
            return cookie_token

        # 3. Check request body for non-GET requests
        if request.method != "GET":
            try:
                # Try to access body if it's JSON
                if hasattr(request, "_json") and request._json:
                    body_token = request._json.get("deviceToken")
                    if body_token and validate_token_format(body_token):
                        logger.debug("Device token extracted from request body")
                        return body_token
            except Exception:
                # Body might not be JSON or accessible, continue without error
                pass

        logger.debug("No valid device token found in request")
        return None

    async def get_optional_session_context(
        self,
        request: Request,
        db: Annotated[AsyncSession, Depends(get_db)],
        authorization: Annotated[Optional[str], Header()] = None,
    ) -> Optional[DeviceSessionContext]:
        """
        Optional device session context dependency.

        This dependency provides device session context if a valid token
        is present, but doesn't require authentication. Use for endpoints
        that benefit from session context but work without it.

        Args:
            request: FastAPI request object
            db: Database session dependency
            authorization: Authorization header dependency

        Returns:
            DeviceSessionContext if valid session, None otherwise
        """
        # Extract token from available sources
        token = await self.extract_device_token(request, authorization)
        if not token:
            return None

        # Validate token and get session context
        try:
            session_context = await self.device_session_service.validate_device_token(
                db, token
            )

            if session_context:
                logger.debug(
                    "Device session context resolved",
                    session_id=str(session_context.session_id),
                    user_id=str(session_context.user_id),
                    workspace_id=session_context.workspace_id,
                )
            else:
                logger.debug("Device token validation failed")

            return session_context

        except Exception as e:
            logger.warning("Error validating device token", error=str(e))
            return None

    async def get_required_session_context(
        self,
        request: Request,
        db: Annotated[AsyncSession, Depends(get_db)],
        authorization: Annotated[Optional[str], Header()] = None,
    ) -> DeviceSessionContext:
        """
        Required device session context dependency.

        This dependency requires a valid device session and will raise
        HTTP 401 if no valid session is found. Use for endpoints that
        require authenticated device sessions.

        Args:
            request: FastAPI request object
            db: Database session dependency
            authorization: Authorization header dependency

        Returns:
            DeviceSessionContext for authenticated session

        Raises:
            HTTPException: 401 if no valid device session found
        """
        session_context = await self.get_optional_session_context(
            request, db, authorization
        )

        if not session_context:
            logger.warning("Required device session not found or invalid")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Valid device session required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return session_context


# Create singleton instance for dependency injection
device_session_dependency = DeviceSessionDependency()

# Convenience dependencies for common use cases
OptionalDeviceSession = Annotated[
    Optional[DeviceSessionContext],
    Depends(device_session_dependency.get_optional_session_context),
]

RequiredDeviceSession = Annotated[
    DeviceSessionContext,
    Depends(device_session_dependency.get_required_session_context),
]


# Legacy alias for backward compatibility
DeviceSessionDep = OptionalDeviceSession
