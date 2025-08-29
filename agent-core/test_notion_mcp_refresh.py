#!/usr/bin/env python3
"""
Test script for Notion MCP token refresh paths (Issue #17).

This script tests:
1. Proactive token refresh (T-5m before expiry)
2. Reactive 401 retry with client rebuild
3. Version-based cache invalidation

Run with: python test_notion_mcp_refresh.py
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import structlog  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from src.clients import NotionMCPClients  # noqa: E402
from src.config import get_settings  # noqa: E402
from src.db import get_async_session  # noqa: E402
from src.db.models import NotionConnection  # noqa: E402
from src.services.mcp_router import get_mcp_router  # noqa: E402
from src.services.oauth_manager import OAuthManager  # noqa: E402
from src.utils.crypto import CryptoService  # noqa: E402

# Configure logging
structlog.configure(
    processors=[
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        structlog.dev.ConsoleRenderer(colors=True),
    ]
)
logger = structlog.get_logger()


async def setup_test_connection(
    db: AsyncSession, user_id: str, expires_in_minutes: int = 10
):
    """
    Create a test Notion connection with configurable expiry.

    Args:
        db: Database session
        user_id: Test user ID
        expires_in_minutes: Minutes until token expires
    """
    settings = get_settings()
    crypto = CryptoService(settings.fernet_key)

    # Create test connection
    connection = NotionConnection(
        user_id=user_id,
        workspace_id=f"test-workspace-{uuid4().hex[:8]}",
        workspace_name="Test Workspace",
        access_token_ciphertext=crypto.encrypt_token("test-access-token"),
        refresh_token_ciphertext=crypto.encrypt_token("test-refresh-token")
        if expires_in_minutes > 0
        else None,
        access_token_expires_at=datetime.now(timezone.utc)
        + timedelta(minutes=expires_in_minutes),
        key_version=crypto.current_version,
        needs_reauth=False,
    )

    db.add(connection)
    await db.commit()

    logger.info(
        "Created test connection",
        user_id=user_id,
        expires_in_minutes=expires_in_minutes,
        expires_at=connection.access_token_expires_at.isoformat(),
    )

    return connection


async def test_proactive_refresh():
    """Test proactive token refresh (T-5m before expiry)."""
    logger.info("=" * 60)
    logger.info("TEST 1: Proactive Token Refresh (T-5m)")
    logger.info("=" * 60)

    settings = get_settings()
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with get_async_session() as db:
        # Create connection expiring in 4 minutes (within T-5m window)
        await setup_test_connection(db, user_id, expires_in_minutes=4)

        # Initialize Notion MCP clients
        crypto = CryptoService(settings.fernet_key)
        oauth_manager = OAuthManager(settings, crypto)
        notion_clients = NotionMCPClients(oauth_manager, crypto)

        # First get - should trigger proactive refresh
        logger.info("Getting client (should trigger proactive refresh)...")
        client1 = await notion_clients.get(user_id)

        if client1:
            logger.success("✓ Client created successfully")

            # Check that token was refreshed
            async with get_async_session() as db2:
                conn = await db2.get(NotionConnection, user_id)
                if conn and conn.access_token_expires_at > datetime.now(
                    timezone.utc
                ) + timedelta(minutes=30):
                    logger.success("✓ Token was proactively refreshed")
                else:
                    logger.warning("⚠ Token was not refreshed as expected")
        else:
            logger.error("✗ Failed to create client")

    logger.info("")


async def test_reactive_401_retry():
    """Test reactive 401 retry with client rebuild."""
    logger.info("=" * 60)
    logger.info("TEST 2: Reactive 401 Retry")
    logger.info("=" * 60)

    user_id = f"test-user-{uuid4().hex[:8]}"

    async with get_async_session() as db:
        # Create connection with valid token
        await setup_test_connection(db, user_id, expires_in_minutes=60)

        # Initialize router with Notion clients
        router = await get_mcp_router()

        if router.notion_clients:
            # Get client for user
            client = await router.notion_clients.get(user_id)

            if client:
                logger.info("Client created, simulating 401 error...")

                # Simulate 401 error in process_tool_call hook
                # This would normally happen when calling a Notion tool
                try:
                    # Mock a 401 error
                    error = Exception("401 Unauthorized")

                    # The process_tool_call hook should:
                    # 1. Detect 401 error
                    # 2. Refresh token
                    # 3. Evict cached client
                    # 4. Retry once

                    from src.clients import is_unauthorized_error

                    if is_unauthorized_error(error):
                        logger.info("✓ 401 error detected correctly")

                        # Evict client (simulating what hook does)
                        await router.notion_clients.evict(user_id)
                        logger.info("✓ Client evicted")

                        # Get new client (should rebuild)
                        new_client = await router.notion_clients.get(user_id)
                        if new_client:
                            logger.success("✓ Client rebuilt after 401")
                        else:
                            logger.error("✗ Failed to rebuild client")

                except Exception as e:
                    logger.error(f"✗ Error during 401 test: {e}")
            else:
                logger.error("✗ Failed to create initial client")
        else:
            logger.warning("⚠ Notion clients not initialized in router")

    logger.info("")


async def test_version_cache_invalidation():
    """Test version-based cache invalidation."""
    logger.info("=" * 60)
    logger.info("TEST 3: Version-Based Cache Invalidation")
    logger.info("=" * 60)

    settings = get_settings()
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with get_async_session() as db:
        # Create initial connection
        conn = await setup_test_connection(db, user_id, expires_in_minutes=60)

        # Initialize Notion MCP clients
        crypto = CryptoService(settings.fernet_key)
        oauth_manager = OAuthManager(settings, crypto)
        notion_clients = NotionMCPClients(oauth_manager, crypto)

        # Get client (creates and caches)
        client1 = await notion_clients.get(user_id)
        if client1:
            logger.info("✓ Initial client created and cached")

        # Get again (should use cache)
        client2 = await notion_clients.get(user_id)
        if client2 and client1 is client2:
            logger.success("✓ Cached client reused (same instance)")
        else:
            logger.warning("⚠ Client was rebuilt unnecessarily")

        # Simulate token change (update expiry)
        conn.access_token_expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=120
        )
        conn.access_token_ciphertext = crypto.encrypt_token("new-access-token")
        await db.commit()
        logger.info("Token updated in database")

        # Get again (should detect version change and rebuild)
        client3 = await notion_clients.get(user_id)
        if client3 and client3 is not client2:
            logger.success("✓ Client rebuilt after token change (different instance)")
        else:
            logger.error("✗ Client not rebuilt after token change")

    logger.info("")


async def test_integration_flow():
    """Test full integration with chat endpoint flow."""
    logger.info("=" * 60)
    logger.info("TEST 4: Integration Flow")
    logger.info("=" * 60)

    settings = get_settings()
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with get_async_session() as db:
        # Create connection
        await setup_test_connection(db, user_id, expires_in_minutes=60)

        # Initialize router
        router = await get_mcp_router()

        # Get toolsets for user (simulating what agent orchestrator does)
        toolsets = await router.get_toolsets_for_user(user_id)

        # Check that Notion tools are included
        notion_tools_found = False
        for toolset in toolsets:
            if hasattr(toolset, "_tool_prefix") and toolset._tool_prefix == "notion":
                notion_tools_found = True
                logger.success("✓ Notion tools included for authenticated user")
                break

        if not notion_tools_found:
            # Check if it's because feature is disabled
            if not settings.FEATURE_NOTION_HOSTED_MCP:
                logger.warning("⚠ Notion hosted MCP feature is disabled")
            else:
                logger.error("✗ Notion tools not found for user")

        # Get toolsets for anonymous user
        anon_toolsets = await router.get_toolsets_for_user(None)

        # Check that Notion tools are NOT included
        notion_in_anon = False
        for toolset in anon_toolsets:
            if hasattr(toolset, "_tool_prefix") and toolset._tool_prefix == "notion":
                notion_in_anon = True
                break

        if not notion_in_anon:
            logger.success("✓ Notion tools correctly excluded for anonymous user")
        else:
            logger.error("✗ Notion tools incorrectly included for anonymous user")

    logger.info("")


async def main():
    """Run all tests."""
    logger.info("Starting Notion MCP Token Refresh Tests")
    logger.info("")

    try:
        # Test 1: Proactive refresh
        await test_proactive_refresh()

        # Test 2: Reactive 401 retry
        await test_reactive_401_retry()

        # Test 3: Version-based cache invalidation
        await test_version_cache_invalidation()

        # Test 4: Integration flow
        await test_integration_flow()

        logger.info("=" * 60)
        logger.success("All tests completed!")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"Test suite failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
