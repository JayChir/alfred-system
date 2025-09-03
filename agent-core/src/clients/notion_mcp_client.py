"""
Notion MCP client factory for per-user authenticated connections.

This module provides a lightweight factory for creating and managing
per-user Notion MCP clients using OAuth tokens. It integrates with
Notion's hosted MCP service at https://mcp.notion.com/mcp.

Key features:
- Per-user client isolation with OAuth Bearer tokens
- Automatic client rebuild on token refresh
- Version tracking to detect token changes
- Single-flight locks to prevent duplicate creation
- Integration with existing token refresh infrastructure
"""

import asyncio
import hashlib
from typing import Dict, Optional, Tuple

import httpx
import structlog
from pydantic_ai.mcp import MCPServerStreamableHTTP

from ..db import get_async_session
from ..services.oauth_manager import OAuthManager
from ..utils.crypto import CryptoService

logger = structlog.get_logger(__name__)

# Notion's hosted MCP endpoints (no custom server needed)
NOTION_MCP_HTTP = "https://mcp.notion.com/mcp"  # Streamable HTTP (recommended)
NOTION_MCP_SSE = "https://mcp.notion.com/sse"  # Server-Sent Events (alternative)


class NotionMCPClients:
    """
    Lightweight per-user cache of MCP clients for Notion's hosted service.

    This factory:
    - Creates authenticated MCP clients using OAuth Bearer tokens
    - Manages client lifecycle with version tracking
    - Rebuilds clients when tokens change
    - Uses Notion's official hosted MCP endpoint
    - Integrates with existing token refresh infrastructure

    The factory does NOT:
    - Implement a custom Notion server
    - Handle token refresh (delegated to OAuthManager)
    - Manage background cleanup (stateless HTTP)
    """

    def __init__(
        self,
        oauth_manager: OAuthManager,
        crypto_service: CryptoService,
        http_timeout: Optional[httpx.Timeout] = None,
        http_limits: Optional[httpx.Limits] = None,
    ):
        """
        Initialize the Notion MCP client factory.

        Args:
            oauth_manager: OAuth manager for token refresh
            crypto_service: Crypto service for token decryption
            http_timeout: HTTP client timeout configuration
            http_limits: HTTP client connection limits
        """
        self.oauth = oauth_manager
        self.crypto = crypto_service

        # Per-user state management
        self._locks: Dict[str, asyncio.Lock] = {}  # Single-flight locks
        self._clients: Dict[
            str, Tuple[str, MCPServerStreamableHTTP]
        ] = {}  # user_id -> (version, client)

        # HTTP client configuration (reasonable defaults for MVP)
        self.http_timeout = http_timeout or httpx.Timeout(
            connect=5.0,  # 5s connection timeout
            read=30.0,  # 30s read timeout (not 100s)
            write=30.0,  # 30s write timeout
            pool=30.0,  # 30s pool timeout
        )
        self.http_limits = http_limits or httpx.Limits(
            max_connections=50,
            max_keepalive_connections=20,
        )

    def _compute_version(self, connection, token: str) -> str:
        """
        Compute version string to detect token/expiry changes.

        This version changes when:
        - Token is refreshed (different token suffix)
        - Token expiry changes
        - Encryption key version changes

        Args:
            connection: NotionConnection with token metadata
            token: Decrypted access token

        Returns:
            Version string for cache invalidation
        """
        # Use token suffix + expiry + key version for change detection
        exp = (
            int(connection.access_token_expires_at.timestamp())
            if connection.access_token_expires_at
            else 0
        )
        version_data = f"{connection.key_version}:{token[-10:]}:{exp}"
        digest = hashlib.sha256(version_data.encode()).hexdigest()[:16]

        logger.debug(
            "Computed client version",
            user_id=str(connection.user_id),
            key_version=connection.key_version,
            expires_at=exp,
            version=digest,
        )

        return digest

    def _create_http_client(self, access_token: str) -> httpx.AsyncClient:
        """
        Create HTTP client with Notion OAuth Bearer token.

        Args:
            access_token: Decrypted OAuth access token

        Returns:
            Configured httpx.AsyncClient with auth headers
        """
        return httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "AlfredAgentCore/0.1",
                # Note: Do NOT add Notion-Version header (REST API only)
            },
            timeout=self.http_timeout,
            limits=self.http_limits,
            follow_redirects=True,
        )

    async def get(self, user_id: str) -> Optional[MCPServerStreamableHTTP]:
        """
        Get or create MCP client for user with fresh token.

        This method:
        1. Ensures token freshness (proactive T-5m refresh)
        2. Checks version to detect token changes
        3. Creates new client if needed
        4. Returns cached client if version unchanged

        Args:
            user_id: User ID to get client for

        Returns:
            Configured MCP client or None if no connection
        """
        # Single-flight lock to prevent duplicate creation
        lock = self._locks.setdefault(user_id, asyncio.Lock())

        async with lock:
            try:
                # Get fresh token using existing infrastructure
                async with get_async_session() as db:
                    connections = await self.oauth.ensure_token_fresh(db, user_id)

                    # Find Notion connection
                    notion_conn = None
                    for conn in connections:
                        if conn.provider == "notion" and not conn.needs_reauth:
                            notion_conn = conn
                            break

                    if not notion_conn:
                        logger.debug(
                            "No valid Notion connection for user",
                            user_id=user_id,
                        )
                        return None

                    # Decrypt access token
                    access_token = self.crypto.decrypt_token(
                        notion_conn.access_token_ciphertext
                    )

                    # Compute version for change detection
                    version = self._compute_version(notion_conn, access_token)

                    # Check cached client
                    cached = self._clients.get(user_id)
                    if cached and cached[0] == version:
                        logger.debug(
                            "Using cached Notion MCP client",
                            user_id=user_id,
                            version=version,
                        )
                        return cached[1]

                    # Create new client (version changed or first access)
                    logger.info(
                        "Creating new Notion MCP client",
                        user_id=user_id,
                        version=version,
                        workspace_id=notion_conn.workspace_id,
                        workspace_name=notion_conn.workspace_name,
                    )

                    # Create authenticated HTTP client
                    http_client = self._create_http_client(access_token)

                    # Create MCP client for Notion's hosted service
                    mcp_client = MCPServerStreamableHTTP(
                        url=NOTION_MCP_HTTP,  # Correct endpoint
                        http_client=http_client,
                        tool_prefix="notion",  # Stable prefix (not per-user)
                        # Note: process_tool_call hook stays in router
                    )

                    # Cache the client with version
                    self._clients[user_id] = (version, mcp_client)

                    # Note: No __aenter__ needed for streamable HTTP (stateless)

                    return mcp_client

            except Exception as e:
                logger.error(
                    "Failed to create Notion MCP client",
                    user_id=user_id,
                    error=str(e),
                    exc_info=True,
                )
                return None

    async def evict(self, user_id: str) -> None:
        """
        Evict cached client to force rebuild on next get().

        Used after 401 errors to ensure fresh auth headers.

        Args:
            user_id: User ID to evict client for
        """
        evicted = self._clients.pop(user_id, None)
        if evicted:
            logger.info(
                "Evicted Notion MCP client",
                user_id=user_id,
                old_version=evicted[0],
            )

    async def evict_all(self) -> None:
        """
        Evict all cached clients.

        Used during shutdown or for testing.
        """
        count = len(self._clients)
        self._clients.clear()
        logger.info("Evicted all Notion MCP clients", count=count)

    def get_stats(self) -> Dict[str, any]:
        """
        Get factory statistics for monitoring.

        Returns:
            Dictionary with client cache stats
        """
        return {
            "cached_clients": len(self._clients),
            "active_locks": len(
                [lock for lock in self._locks.values() if lock.locked()]
            ),
            "user_ids": list(self._clients.keys()),
        }


def is_unauthorized_error(error: Exception) -> bool:
    """
    Check if an error indicates unauthorized access (401).

    Args:
        error: Exception from MCP call

    Returns:
        True if error indicates 401/unauthorized
    """
    error_str = str(error).lower()

    # Check for common 401 indicators
    unauthorized_indicators = [
        "401",
        "unauthorized",
        "authentication failed",
        "invalid token",
        "token expired",
        "access denied",
    ]

    return any(indicator in error_str for indicator in unauthorized_indicators)


def is_auth_or_transport_error(result: any) -> bool:
    """
    Check if a result indicates auth or transport failure.

    These should never be cached.

    Args:
        result: Result from MCP call

    Returns:
        True if result should not be cached
    """
    if isinstance(result, Exception):
        return True

    if isinstance(result, dict):
        # Check for error indicators in result
        if result.get("error") or result.get("status") == "error":
            return True

        # Check for HTTP error codes
        if isinstance(result.get("status_code"), int):
            status = result["status_code"]
            if status >= 400:  # 4xx and 5xx errors
                return True

    return False


# Global factory instance
_notion_clients: Optional[NotionMCPClients] = None


async def get_notion_mcp_clients() -> NotionMCPClients:
    """
    Get the global Notion MCP clients factory instance.

    Returns:
        NotionMCPClients singleton instance
    """
    global _notion_clients

    if _notion_clients is None:
        from ..config import get_settings
        from ..services.oauth_manager import OAuthManager

        settings = get_settings()
        crypto_service = CryptoService(settings.fernet_key)
        oauth_manager = OAuthManager(settings, crypto_service)

        _notion_clients = NotionMCPClients(oauth_manager, crypto_service)

    return _notion_clients
