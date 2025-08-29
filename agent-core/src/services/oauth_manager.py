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
import random
import secrets
import time
from asyncio import Lock
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, NamedTuple, Optional
from urllib.parse import urlencode

import httpx
import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.models import NotionConnection, OAuthState
from ..utils.alerting import get_alert_manager
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


class RefreshResult(NamedTuple):
    """Result of a token refresh operation with detailed status classification."""

    success: bool
    error: Optional[str] = None
    classification: str = "success"  # "success", "terminal", "transient"
    token_response: Optional[Dict[str, Any]] = None


class RefreshMetrics:
    """Production-grade metrics collection for token refresh operations."""

    def __init__(self):
        self.refresh_attempts_total = 0
        self.refresh_success_total = 0
        self.refresh_failures = defaultdict(int)  # by error classification
        self.refresh_latencies = []  # last 100 latencies for avg calculation
        self.tokens_expiring_5m = 0
        self.preflight_refresh_rate = 0.0

    def record_success(self, latency_ms: float) -> None:
        """Record a successful refresh operation."""
        self.refresh_attempts_total += 1
        self.refresh_success_total += 1
        self.refresh_latencies.append(latency_ms)
        # Keep only last 100 latencies to prevent memory growth
        if len(self.refresh_latencies) > 100:
            self.refresh_latencies = self.refresh_latencies[-100:]

    def record_failure(self, classification: str) -> None:
        """Record a failed refresh operation."""
        self.refresh_attempts_total += 1
        self.refresh_failures[classification] += 1

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get comprehensive metrics summary for health monitoring."""
        return {
            "refresh_attempts_total": self.refresh_attempts_total,
            "refresh_success_total": self.refresh_success_total,
            "success_rate": (
                self.refresh_success_total / max(1, self.refresh_attempts_total)
            ),
            "avg_latency_ms": (
                sum(self.refresh_latencies) / max(1, len(self.refresh_latencies))
            ),
            "failures_by_reason": dict(self.refresh_failures),
            "tokens_expiring_soon": self.tokens_expiring_5m,
            "preflight_refresh_rate": self.preflight_refresh_rate,
        }


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
            timeout=httpx.Timeout(
                connect=3.0, read=10.0, write=10.0, pool=10.0
            ),  # Explicit timeouts for all phases
            follow_redirects=False,  # OAuth requires manual redirect handling
        )

        # State TTL configuration (10 minutes for OAuth flows)
        self.state_ttl = timedelta(minutes=10)

        # Token refresh infrastructure (Phase 2 - Issue #16)
        self._refresh_locks: Dict[str, Lock] = {}
        self.refresh_metrics = RefreshMetrics()

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

    def analyze_token_capabilities(
        self, token_response: Dict[str, Any]
    ) -> tuple[bool, Optional[datetime]]:
        """
        Analyze token response to determine refresh capabilities and expiration.

        Args:
            token_response: Token response from Notion OAuth API

        Returns:
            Tuple of (has_refresh_capability, access_token_expires_at)
        """
        # Check if refresh token is available
        has_refresh_token = bool(token_response.get("refresh_token"))

        # Calculate access token expiration if expires_in is provided
        expires_in = token_response.get("expires_in")
        expires_at = None
        if expires_in:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            logger.info(
                "Token expiration detected",
                expires_in_seconds=expires_in,
                expires_at=expires_at.isoformat(),
                has_refresh_token=has_refresh_token,
            )

        # Log refresh capability for debugging
        logger.info(
            "Token capability analysis",
            has_refresh_token=has_refresh_token,
            has_expires_in=bool(expires_in),
            refresh_capable=has_refresh_token and bool(expires_in),
        )

        return has_refresh_token, expires_at

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

        # Analyze token capabilities and expiration (Phase 1 - Issue #16)
        (
            has_refresh_capability,
            access_token_expires_at,
        ) = self.analyze_token_capabilities(token_response)
        refresh_token_expires_at = None  # Notion doesn't provide refresh token expiry

        # Create connection record with refresh capability tracking
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
            # Initialize refresh tracking fields (Phase 1 - Issue #16)
            supports_refresh=has_refresh_capability,
            refresh_failure_count=0,
            needs_reauth=False,
            last_refresh_attempt=None,
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
            # Update existing connection with new tokens and refresh capability
            existing_connection.workspace_id = workspace_id
            existing_connection.workspace_name = workspace_name
            existing_connection.access_token_ciphertext = access_token_ciphertext
            existing_connection.refresh_token_ciphertext = refresh_token_ciphertext
            existing_connection.access_token_expires_at = access_token_expires_at
            existing_connection.refresh_token_expires_at = refresh_token_expires_at
            existing_connection.scopes = connection.scopes

            # Update refresh capability and reset refresh tracking on new token
            existing_connection.update_refresh_capability(has_refresh_capability)
            existing_connection.refresh_failure_count = 0  # Reset failures on new token
            existing_connection.needs_reauth = False  # Clear re-auth requirement

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

    # ===== Token Refresh Infrastructure (Phase 2 - Issue #16) =====

    def _get_refresh_lock(self, connection_id: str) -> Lock:
        """
        Get or create a lock for single-flight refresh per connection.

        Args:
            connection_id: Connection ID to get lock for

        Returns:
            Async lock for this connection
        """
        if connection_id not in self._refresh_locks:
            self._refresh_locks[connection_id] = Lock()
        return self._refresh_locks[connection_id]

    def is_token_expiring_soon(
        self, connection: NotionConnection, window_minutes: Optional[int] = None
    ) -> bool:
        """
        Check if token is expiring soon with configurable jitter and clock skew tolerance.

        Args:
            connection: Notion connection to check
            window_minutes: Time window to check expiry (uses config default if None)

        Returns:
            True if token will expire within the window (with jitter)
        """
        # Only check expiry for connections that support refresh
        if not connection.access_token_expires_at or not connection.supports_refresh:
            return False

        now = datetime.now(timezone.utc)

        # Use configurable settings for refresh timing
        window_minutes = window_minutes or self.settings.oauth_refresh_window_minutes
        clock_skew_seconds = self.settings.oauth_refresh_clock_skew_seconds
        jitter_seconds = self.settings.oauth_refresh_jitter_seconds

        # Clock skew tolerance: subtract configured seconds from expiry
        adjusted_expiry = connection.access_token_expires_at - timedelta(
            seconds=clock_skew_seconds
        )

        # Add jitter to prevent thundering herd: ±configured seconds
        jitter_offset = random.randint(-jitter_seconds, jitter_seconds)
        jittered_window = timedelta(minutes=window_minutes, seconds=jitter_offset)

        expires_in = adjusted_expiry - now
        is_expiring = expires_in <= jittered_window

        if is_expiring:
            logger.info(
                "Token expiring soon detected",
                connection_id=str(connection.id),
                expires_at=connection.access_token_expires_at.isoformat(),
                expires_in_seconds=int(expires_in.total_seconds()),
                jitter_applied=jitter_offset,
                window_minutes=window_minutes,
                clock_skew_seconds=clock_skew_seconds,
            )

        return is_expiring

    async def get_user_active_connections(
        self, db: AsyncSession, user_id: str
    ) -> list[NotionConnection]:
        """
        Get all active Notion connections for a user.

        Args:
            db: Database session
            user_id: User ID to get connections for

        Returns:
            List of active NotionConnection records
        """
        stmt = select(NotionConnection).where(
            NotionConnection.user_id == user_id,
            NotionConnection.revoked_at.is_(None),  # Only active connections
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def refresh_notion_token_with_backoff(
        self, connection: NotionConnection, retry_count: int = 0
    ) -> RefreshResult:
        """
        Refresh Notion token with configurable exponential backoff and error classification.

        Args:
            connection: Connection to refresh
            retry_count: Current retry attempt (for exponential backoff)

        Returns:
            RefreshResult with success/error status and classification
        """
        max_retries = self.settings.oauth_refresh_max_retries
        base_delay_seconds = self.settings.oauth_refresh_base_delay_ms / 1000.0

        try:
            # Decrypt refresh token for API call
            refresh_token = self.crypto.decrypt_token(
                connection.refresh_token_ciphertext
            )

            # Prepare HTTP Basic auth (same as initial token exchange)
            credentials = (
                f"{self.settings.notion_client_id}:{self.settings.notion_client_secret}"
            )
            credentials_b64 = base64.b64encode(credentials.encode()).decode()

            headers = {
                "Authorization": f"Basic {credentials_b64}",
                "Content-Type": "application/json",
            }

            # Prepare refresh request payload
            payload = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }

            logger.info(
                "Attempting token refresh",
                connection_id=str(connection.id),
                retry_count=retry_count,
            )

            # Execute token refresh request
            response = await self.http_client.post(
                self.settings.notion_token_url, json=payload, headers=headers
            )

            # Handle successful response
            if response.status_code == 200:
                token_response = response.json()
                logger.info(
                    "Token refresh successful",
                    connection_id=str(connection.id),
                    has_new_refresh_token=bool(token_response.get("refresh_token")),
                )
                return RefreshResult(
                    success=True,
                    classification="success",
                    token_response=token_response,
                )

            # Handle 400 Bad Request - check for terminal vs transient errors
            elif response.status_code == 400:
                error_data = response.json()
                error_code = error_data.get("error", "")

                # Terminal errors requiring immediate re-authentication
                if error_code in ["invalid_grant", "invalid_client", "invalid_token"]:
                    logger.warning(
                        "Terminal refresh error",
                        connection_id=str(connection.id),
                        error_code=error_code,
                        status_code=response.status_code,
                    )
                    return RefreshResult(
                        success=False,
                        error=f"Terminal: {error_code}",
                        classification="terminal",
                    )

                # Other 400 errors are transient (malformed request, etc.)
                logger.warning(
                    "Transient refresh error",
                    connection_id=str(connection.id),
                    error_code=error_code,
                    status_code=response.status_code,
                )
                return RefreshResult(
                    success=False,
                    error=f"Transient: {error_code}",
                    classification="transient",
                )

            # Handle 429 Rate Limited - retry with exponential backoff
            elif response.status_code == 429:
                if retry_count < max_retries:
                    retry_after = int(response.headers.get("Retry-After", "60"))
                    sleep_duration = min(retry_after, 300)  # Cap at 5 minutes

                    logger.warning(
                        "Rate limited, retrying after delay",
                        connection_id=str(connection.id),
                        retry_after=retry_after,
                        sleep_duration=sleep_duration,
                        retry_count=retry_count,
                    )

                    import asyncio

                    await asyncio.sleep(sleep_duration)
                    return await self.refresh_notion_token_with_backoff(
                        connection, retry_count + 1
                    )

                # Max retries reached
                return RefreshResult(
                    success=False,
                    error="Rate limited - max retries exceeded",
                    classification="transient",
                )

            # All other HTTP errors are transient
            else:
                logger.warning(
                    "HTTP error during token refresh",
                    connection_id=str(connection.id),
                    status_code=response.status_code,
                    error_detail=response.text[:200],
                )
                return RefreshResult(
                    success=False,
                    error=f"HTTP {response.status_code}",
                    classification="transient",
                )

        except httpx.RequestError as e:
            # Network errors - retry with exponential backoff
            if retry_count < max_retries:
                delay = base_delay_seconds * (4**retry_count)  # Exponential backoff
                logger.warning(
                    "Network error, retrying with backoff",
                    connection_id=str(connection.id),
                    error=str(e),
                    delay_seconds=delay,
                    retry_count=retry_count,
                )

                import asyncio

                await asyncio.sleep(delay)
                return await self.refresh_notion_token_with_backoff(
                    connection, retry_count + 1
                )

            # Max retries reached
            return RefreshResult(
                success=False,
                error=f"Network: {e}",
                classification="transient",
            )

        except Exception as e:
            # Unexpected errors are treated as transient
            logger.error(
                "Unexpected error during token refresh",
                connection_id=str(connection.id),
                error=str(e),
            )
            return RefreshResult(
                success=False,
                error=f"Unexpected: {e}",
                classification="transient",
            )

    async def update_refreshed_tokens(
        self,
        db: AsyncSession,
        connection: NotionConnection,
        token_response: Dict[str, Any],
    ) -> NotionConnection:
        """
        Atomically update connection with refreshed tokens.

        Args:
            db: Database session
            connection: Connection to update
            token_response: New token response from refresh

        Returns:
            Updated NotionConnection with new tokens
        """
        # Extract tokens from response
        new_access_token = token_response["access_token"]
        new_refresh_token = token_response.get("refresh_token")

        # Compute new expiry with absolute timestamp
        expires_in = token_response.get("expires_in")
        new_expires_at = None
        if expires_in:
            new_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        # Encrypt tokens with current crypto key (maintain same key_version)
        access_ciphertext = self.crypto.encrypt_token(new_access_token)
        connection.access_token_ciphertext = access_ciphertext

        if new_refresh_token:
            refresh_ciphertext = self.crypto.encrypt_token(new_refresh_token)
            connection.refresh_token_ciphertext = refresh_ciphertext

        # Update expiration and refresh tracking
        connection.access_token_expires_at = new_expires_at
        connection.mark_refresh_success()  # Updates last_refresh_attempt, resets counters

        # Commit changes atomically
        await db.commit()
        await db.refresh(connection)

        logger.info(
            "Token refresh completed and stored",
            connection_id=str(connection.id),
            new_expires_at=new_expires_at.isoformat() if new_expires_at else None,
            has_new_refresh_token=bool(new_refresh_token),
        )

        return connection

    async def ensure_token_fresh(
        self, db: AsyncSession, user_id: str
    ) -> list[NotionConnection]:
        """
        Ensure all user's tokens are fresh with single-flight refresh per connection.

        This is the main entry point for on-demand token refresh. It implements
        single-flight refresh to prevent duplicate network calls when multiple
        requests arrive simultaneously. Also coordinates with background refresh
        service to avoid duplicate work (Phase 4 - Issue #16).

        Args:
            db: Database session
            user_id: User ID to refresh tokens for

        Returns:
            List of refreshed connections (only those that were actually refreshed)
        """
        # Get all active connections for the user
        connections = await self.get_user_active_connections(db, user_id)
        refreshed_connections = []

        # Process each connection that might need refresh
        for connection in connections:
            # Skip connections that don't support refresh or aren't expiring soon
            if not connection.is_refresh_capable or not self.is_token_expiring_soon(
                connection
            ):
                continue

            connection_id = str(connection.id)

            # Check if background service is already refreshing this connection (Phase 4)
            try:
                from .token_refresh_service import get_token_refresh_service

                background_service = await get_token_refresh_service()
                if background_service.is_connection_being_refreshed(connection_id):
                    logger.debug(
                        "Connection already being refreshed by background service",
                        connection_id=connection_id,
                        user_id=user_id,
                    )
                    continue
            except Exception as e:
                # If background service is not available, continue with on-demand refresh
                logger.debug(
                    "Background service not available for coordination",
                    error=str(e),
                    connection_id=connection_id,
                )

            # Single-flight refresh using per-connection lock
            lock = self._get_refresh_lock(connection_id)

            async with lock:
                # Best-effort cross-process lock using PostgreSQL advisory locks
                # This prevents multiple processes from refreshing the same token
                try:
                    from ..db import try_advisory_lock

                    # Try to acquire advisory lock for this connection
                    if not await try_advisory_lock(db, connection.id):
                        logger.debug(
                            "Advisory lock not acquired, another process is refreshing",
                            connection_id=connection_id,
                            user_id=user_id,
                        )
                        continue
                except ImportError:
                    # Database module doesn't have advisory lock support yet
                    logger.debug(
                        "Advisory locks not available, using in-process locks only",
                        connection_id=connection_id,
                    )
                except Exception as e:
                    # Don't fail if advisory lock fails, just log and continue
                    logger.debug(
                        "Advisory lock attempt failed, continuing with in-process lock",
                        connection_id=connection_id,
                        error=str(e),
                    )

                # Double-check expiry under lock to avoid duplicate refresh
                # Another concurrent request might have already refreshed this token
                await db.refresh(connection)
                if not self.is_token_expiring_soon(connection):
                    logger.info(
                        "Token already refreshed by concurrent request",
                        connection_id=connection_id,
                    )
                    continue

                # Check if connection needs re-auth due to previous failures
                if connection.needs_reauth:
                    logger.warning(
                        "Connection needs re-authentication, skipping refresh",
                        connection_id=connection_id,
                        failure_count=connection.refresh_failure_count,
                    )
                    continue

                # Perform the actual token refresh with timing
                start_time = time.time()
                refresh_result = await self.refresh_notion_token_with_backoff(
                    connection
                )
                latency_ms = (time.time() - start_time) * 1000

                if refresh_result.success:
                    # Update tokens and mark success
                    updated_connection = await self.update_refreshed_tokens(
                        db, connection, refresh_result.token_response
                    )
                    self.refresh_metrics.record_success(latency_ms)
                    refreshed_connections.append(updated_connection)

                    logger.info(
                        "Token refresh successful",
                        connection_id=connection_id,
                        latency_ms=round(latency_ms, 2),
                    )
                else:
                    # Handle refresh failure with configurable threshold
                    is_terminal = refresh_result.classification == "terminal"
                    connection.mark_refresh_failure(is_terminal)

                    # Check if failure count exceeds configured threshold
                    if (
                        connection.refresh_failure_count
                        >= self.settings.oauth_max_failure_count
                    ):
                        connection.needs_reauth = True
                        logger.warning(
                            "Connection marked for re-auth due to failure threshold",
                            connection_id=connection_id,
                            failure_count=connection.refresh_failure_count,
                            threshold=self.settings.oauth_max_failure_count,
                        )

                    await db.commit()
                    self.refresh_metrics.record_failure(refresh_result.classification)

                    # Send alert for refresh failures (production monitoring)
                    alert_manager = get_alert_manager()
                    alert_manager.alert_token_refresh_failure(
                        user_id=str(connection.user_id),
                        connection_id=connection_id,
                        failure_count=connection.refresh_failure_count,
                        error_message=refresh_result.error or "Unknown error",
                        is_terminal=is_terminal,
                    )

                    logger.warning(
                        "Token refresh failed",
                        connection_id=connection_id,
                        error=refresh_result.error,
                        classification=refresh_result.classification,
                        failure_count=connection.refresh_failure_count,
                        needs_reauth=connection.needs_reauth,
                    )

        # Update metrics for monitoring
        self.refresh_metrics.tokens_expiring_5m = len(
            [
                c
                for c in connections
                if c.supports_refresh and self.is_token_expiring_soon(c)
            ]
        )

        # Send system-wide alerts if many tokens are expiring (production monitoring)
        alert_manager = get_alert_manager()
        alert_manager.alert_high_token_expiry_rate(
            expiring_count=self.refresh_metrics.tokens_expiring_5m,
            total_connections=len(connections),
        )

        # Send alert if overall success rate is low
        if self.refresh_metrics.refresh_attempts_total > 0:
            alert_manager.alert_refresh_success_rate_low(
                success_rate=(
                    self.refresh_metrics.refresh_success_total
                    / self.refresh_metrics.refresh_attempts_total
                ),
                total_attempts=self.refresh_metrics.refresh_attempts_total,
            )

        logger.info(
            "Token refresh sweep completed",
            user_id=user_id,
            connections_checked=len(connections),
            connections_refreshed=len(refreshed_connections),
            tokens_expiring_soon=self.refresh_metrics.tokens_expiring_5m,
        )

        return refreshed_connections
