"""
OAuth Manager service for Alfred Agent Core.

This module handles OAuth flows for external service integrations, starting with Notion.
Provides secure state management, token exchange, and encrypted token storage for use
with hosted MCP services.

Security features:
- Cryptographically secure state tokens with CSRF protection
- HTTP Basic authentication for token exchange
- Encrypted token storage using MultiFernet
- User session binding and TTL enforcement
- Comprehensive error handling with structured logging

OAuth Flow:
1. /connect/{provider} - Generate state, build authorization URL, redirect
2. /oauth/{provider}/callback - Validate state, exchange code for tokens, store encrypted

Token Usage:
- Stored tokens are used to authenticate MCP client connections to hosted services
- For Notion: tokens authenticate with https://mcp.notion.com/mcp
- Per-user token injection enables workspace-specific tool access
"""

import base64
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx
import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.models import NotionConnection, OAuthState
from ..utils.crypto import CryptoService

logger = structlog.get_logger(__name__)


class OAuthManagerError(Exception):
    """Base exception for OAuth Manager operations."""

    pass


class StateValidationError(OAuthManagerError):
    """Raised when OAuth state validation fails."""

    pass


class TokenExchangeError(OAuthManagerError):
    """Raised when OAuth token exchange fails."""

    pass


class OAuthManager:
    """
    OAuth Manager for handling external service authentication flows.

    Supports multiple OAuth providers with consistent security patterns:
    - Secure state management with CSRF protection
    - Encrypted token storage with key rotation
    - Proper error handling and logging
    - User session binding

    Currently supported providers:
    - Notion (backend OAuth with client credentials)
    """

    def __init__(self, settings: Settings, crypto_service: CryptoService):
        """
        Initialize OAuth Manager with settings and crypto service.

        Args:
            settings: Application settings with OAuth configuration
            crypto_service: Crypto service for token encryption
        """
        self.settings = settings
        self.crypto = crypto_service

        # HTTP client for token exchange requests
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=3.0, read=10.0),  # Conservative timeouts
            follow_redirects=False,  # OAuth requires manual redirect handling
        )

        # State TTL configuration (10 minutes for OAuth flows)
        self.state_ttl = timedelta(minutes=10)

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - cleanup HTTP client."""
        await self.http_client.aclose()

    # ===== State Management =====

    def _generate_state_token(self) -> str:
        """
        Generate cryptographically secure state token.

        Returns:
            URL-safe base64-encoded random token (64 characters)
        """
        # Generate 48 random bytes -> 64 character base64 token
        return secrets.token_urlsafe(48)

    async def create_oauth_state(
        self,
        db: AsyncSession,
        provider: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        return_to: Optional[str] = None,
    ) -> OAuthState:
        """
        Create OAuth state record for CSRF protection and user binding.

        Args:
            db: Database session
            provider: OAuth provider name (e.g., 'notion', 'github')
            user_id: Optional user ID for authenticated flows
            session_id: Session ID for user binding
            return_to: Optional return URL after successful auth

        Returns:
            Created OAuthState record with state token
        """
        # Generate secure state token
        state_token = self._generate_state_token()

        # Calculate expiration time
        expires_at = datetime.now(timezone.utc) + self.state_ttl

        # Create state record
        oauth_state = OAuthState(
            state=state_token,
            provider=provider,
            user_id=user_id,
            session_id=session_id,
            return_to=return_to,
            expires_at=expires_at,
        )

        db.add(oauth_state)
        await db.commit()
        await db.refresh(oauth_state)

        logger.info(
            "OAuth state created",
            state_id=str(oauth_state.id),
            provider=provider,
            user_id=user_id,
            expires_at=expires_at.isoformat(),
        )

        return oauth_state

    async def validate_and_consume_state(
        self,
        db: AsyncSession,
        state_token: str,
        provider: str,
        session_id: Optional[str] = None,
    ) -> OAuthState:
        """
        Validate OAuth state token and mark as used.

        Args:
            db: Database session
            state_token: State token from callback
            provider: Expected OAuth provider
            session_id: Optional session ID for binding validation

        Returns:
            Validated OAuthState record

        Raises:
            StateValidationError: If state is invalid, expired, used, or mismatched
        """
        # Query for state record
        stmt = select(OAuthState).where(
            OAuthState.state == state_token, OAuthState.provider == provider
        )
        result = await db.execute(stmt)
        oauth_state = result.scalar_one_or_none()

        if not oauth_state:
            logger.warning(
                "OAuth state not found",
                state_token=state_token[:8] + "...",
                provider=provider,
            )
            raise StateValidationError("Invalid or expired state token")

        # Check expiration
        if oauth_state.is_expired:
            logger.warning(
                "OAuth state expired",
                state_id=str(oauth_state.id),
                expires_at=oauth_state.expires_at.isoformat(),
            )
            raise StateValidationError("State token expired")

        # Check if already used
        if oauth_state.is_used:
            logger.warning(
                "OAuth state already used",
                state_id=str(oauth_state.id),
                used_at=oauth_state.used_at.isoformat(),
            )
            raise StateValidationError("State token already used")

        # Validate session binding if provided
        if session_id and oauth_state.session_id != session_id:
            logger.warning(
                "OAuth state session mismatch",
                state_id=str(oauth_state.id),
                expected_session=session_id,
                actual_session=oauth_state.session_id,
            )
            raise StateValidationError("State token session mismatch")

        # Mark as used
        oauth_state.mark_used()
        await db.commit()

        logger.info(
            "OAuth state validated and consumed",
            state_id=str(oauth_state.id),
            provider=provider,
        )

        return oauth_state

    async def cleanup_expired_states(self, db: AsyncSession) -> int:
        """
        Clean up expired OAuth state records.

        Args:
            db: Database session

        Returns:
            Number of expired states cleaned up
        """
        now = datetime.now(timezone.utc)
        stmt = delete(OAuthState).where(OAuthState.expires_at < now)
        result = await db.execute(stmt)
        await db.commit()

        count = result.rowcount or 0
        if count > 0:
            logger.info("Cleaned up expired OAuth states", count=count)

        return count

    # ===== Notion OAuth Implementation =====

    def build_notion_authorization_url(self, state_token: str) -> str:
        """
        Build Notion OAuth authorization URL with required parameters.

        Args:
            state_token: CSRF state token

        Returns:
            Complete authorization URL for redirect
        """
        # Validate required Notion OAuth configuration
        if not self.settings.notion_client_id:
            raise OAuthManagerError("Notion client ID not configured")
        if not self.settings.notion_redirect_uri:
            raise OAuthManagerError("Notion redirect URI not configured")

        # Build authorization URL with required parameters
        # Note: owner=user is REQUIRED by Notion OAuth
        params = {
            "client_id": self.settings.notion_client_id,
            "redirect_uri": str(self.settings.notion_redirect_uri),
            "response_type": "code",
            "owner": "user",  # Required by Notion
            "state": state_token,
        }

        authorization_url = f"{self.settings.notion_auth_url}?{urlencode(params)}"

        logger.info(
            "Built Notion authorization URL",
            client_id=self.settings.notion_client_id[:8] + "...",
            redirect_uri=str(self.settings.notion_redirect_uri),
            state_token=state_token[:8] + "...",
        )

        return authorization_url

    async def exchange_notion_code_for_tokens(
        self, authorization_code: str
    ) -> Dict[str, Any]:
        """
        Exchange Notion authorization code for access/refresh tokens.

        Uses HTTP Basic authentication with client credentials as required by Notion.

        Args:
            authorization_code: Authorization code from callback

        Returns:
            Token response from Notion API with access_token, bot_id, etc.

        Raises:
            TokenExchangeError: If token exchange fails
        """
        # Validate required configuration
        if not self.settings.notion_client_id or not self.settings.notion_client_secret:
            raise OAuthManagerError("Notion OAuth credentials not configured")

        # Prepare HTTP Basic auth header
        # Format: base64(client_id:client_secret)
        credentials = (
            f"{self.settings.notion_client_id}:{self.settings.notion_client_secret}"
        )
        credentials_b64 = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Authorization": f"Basic {credentials_b64}",
            "Content-Type": "application/json",
            # Note: Do NOT send Notion-Version to token endpoint
            # Notion-Version is only for Data API calls
        }

        # Prepare request payload
        payload = {
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": str(self.settings.notion_redirect_uri),
        }

        try:
            logger.info(
                "Exchanging Notion authorization code",
                client_id=self.settings.notion_client_id[:8] + "...",
                redirect_uri=str(self.settings.notion_redirect_uri),
            )

            # Exchange code for tokens
            response = await self.http_client.post(
                self.settings.notion_token_url, json=payload, headers=headers
            )

            # Check for HTTP errors
            if response.status_code != 200:
                error_detail = response.text[:200]  # Truncate for logging
                logger.error(
                    "Notion token exchange failed",
                    status_code=response.status_code,
                    error_detail=error_detail,
                )
                raise TokenExchangeError(
                    f"Notion token exchange failed with status {response.status_code}"
                )

            # Parse response
            token_response = response.json()

            # Validate required fields
            required_fields = ["access_token", "bot_id", "workspace_id"]
            missing_fields = [
                field for field in required_fields if field not in token_response
            ]
            if missing_fields:
                logger.error(
                    "Notion token response missing required fields",
                    missing_fields=missing_fields,
                    response_keys=list(token_response.keys()),
                )
                raise TokenExchangeError(f"Missing required fields: {missing_fields}")

            logger.info(
                "Notion token exchange successful",
                bot_id=token_response.get("bot_id"),
                workspace_id=token_response.get("workspace_id"),
                has_refresh_token=bool(token_response.get("refresh_token")),
            )

            return token_response

        except httpx.RequestError as e:
            logger.error("Notion token exchange network error", error=str(e))
            raise TokenExchangeError(f"Network error during token exchange: {e}") from e
        except Exception as e:
            logger.error("Notion token exchange unexpected error", error=str(e))
            raise TokenExchangeError(
                f"Unexpected error during token exchange: {e}"
            ) from e

    async def store_notion_connection(
        self, db: AsyncSession, user_id: str, token_response: Dict[str, Any]
    ) -> NotionConnection:
        """
        Store Notion connection with encrypted tokens for MCP client authentication.

        These tokens will be used by MCP clients to authenticate with Notion's
        hosted MCP service at https://mcp.notion.com/mcp.

        Args:
            db: Database session
            user_id: User ID for the connection
            token_response: Token response from Notion API

        Returns:
            Created NotionConnection record with encrypted tokens
        """
        # Extract required fields from token response
        access_token = token_response["access_token"]
        bot_id = token_response["bot_id"]
        workspace_id = token_response["workspace_id"]

        # Optional fields
        refresh_token = token_response.get("refresh_token")
        workspace_name = token_response.get("workspace_name")
        # owner = token_response.get("owner", {})  # Unused for now

        # Encrypt tokens using MultiFernet
        access_token_ciphertext = self.crypto.encrypt_token(access_token)
        refresh_token_ciphertext = None
        if refresh_token:
            refresh_token_ciphertext = self.crypto.encrypt_token(refresh_token)

        # Handle token expiration (Notion doesn't typically provide expiry info)
        access_token_expires_at = None
        refresh_token_expires_at = None

        # Create connection record
        connection = NotionConnection(
            user_id=user_id,
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            bot_id=bot_id,
            access_token_ciphertext=access_token_ciphertext,
            refresh_token_ciphertext=refresh_token_ciphertext,
            access_token_expires_at=access_token_expires_at,
            refresh_token_expires_at=refresh_token_expires_at,
            scopes=token_response.get("scope", "").split(",")
            if token_response.get("scope")
            else [],
        )

        # Check for existing connection and update or create
        # Use (user_id, bot_id) uniqueness as recommended by Notion
        stmt = select(NotionConnection).where(
            NotionConnection.user_id == user_id,
            NotionConnection.bot_id == bot_id,
            NotionConnection.revoked_at.is_(None),
        )
        result = await db.execute(stmt)
        existing_connection = result.scalar_one_or_none()

        if existing_connection:
            # Update existing connection
            existing_connection.workspace_id = workspace_id
            existing_connection.workspace_name = workspace_name
            existing_connection.access_token_ciphertext = access_token_ciphertext
            existing_connection.refresh_token_ciphertext = refresh_token_ciphertext
            existing_connection.access_token_expires_at = access_token_expires_at
            existing_connection.refresh_token_expires_at = refresh_token_expires_at
            existing_connection.scopes = connection.scopes

            await db.commit()
            await db.refresh(existing_connection)

            logger.info(
                "Updated existing Notion connection",
                connection_id=str(existing_connection.id),
                user_id=user_id,
                bot_id=bot_id,
                workspace_id=workspace_id,
            )

            return existing_connection
        else:
            # Create new connection
            db.add(connection)
            await db.commit()
            await db.refresh(connection)

            logger.info(
                "Created new Notion connection",
                connection_id=str(connection.id),
                user_id=user_id,
                bot_id=bot_id,
                workspace_id=workspace_id,
            )

            return connection

    async def validate_notion_token(
        self, access_token: str
    ) -> Optional[Dict[str, Any]]:
        """
        Validate Notion access token by calling /v1/users/me.

        This is an optional post-success sanity check recommended for debugging.

        Args:
            access_token: Decrypted access token

        Returns:
            User info from Notion API, or None if validation fails
        """
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Notion-Version": "2022-06-28",  # Required for Data API calls
        }

        try:
            response = await self.http_client.get(
                "https://api.notion.com/v1/users/me", headers=headers
            )

            if response.status_code == 200:
                user_info = response.json()
                logger.info(
                    "Notion token validation successful",
                    user_type=user_info.get("type"),
                    user_id=user_info.get("id"),
                )
                return user_info
            else:
                logger.warning(
                    "Notion token validation failed",
                    status_code=response.status_code,
                    error=response.text[:100],
                )
                return None

        except Exception as e:
            logger.warning("Notion token validation error", error=str(e))
            return None
