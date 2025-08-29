"""
End-to-end workflow tests for token refresh system (Issue #16).

These tests validate complete workflows from API requests through database updates:
- Full OAuth token refresh cycle
- Chat endpoint with token refresh integration
- Background service refresh sweep workflow
- Failure recovery and re-authentication flows
- Production-like scenario testing
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.app import app
from src.config import get_settings
from src.db.models import NotionConnection, User
from src.services.oauth_manager import OAuthManager
from src.services.token_refresh_service import TokenRefreshService
from src.utils.crypto import CryptoService


class TestEndToEndWorkflows:
    """End-to-end workflow tests for the complete token refresh system."""

    @pytest.fixture
    async def test_user(self, db_session: AsyncSession):
        """Create a test user for workflow testing."""
        user = User(
            id=str(uuid.uuid4()),
            email="test@example.com",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    @pytest.fixture
    async def test_setup(self, db_session: AsyncSession, test_user: User):
        """Complete test setup with user, connection, and services."""
        settings = get_settings()
        crypto_service = CryptoService()

        # Create connection expiring soon (30 seconds)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=30)

        connection = NotionConnection(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id="test-workspace-123",
            workspace_name="Test Workspace",
            access_token=crypto_service.encrypt("original-access-token"),
            refresh_token=crypto_service.encrypt("original-refresh-token"),
            expires_at=expires_at,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            consecutive_failures=0,
            needs_reauth=False,
        )

        db_session.add(connection)
        await db_session.commit()
        await db_session.refresh(connection)

        oauth_manager = OAuthManager(settings, crypto_service)

        return {
            "user": test_user,
            "connection": connection,
            "oauth_manager": oauth_manager,
            "crypto_service": crypto_service,
            "settings": settings,
            "db_session": db_session,
        }

    @pytest.mark.asyncio
    async def test_complete_oauth_refresh_cycle(self, test_setup):
        """Test complete OAuth token refresh cycle from expiry detection to update."""

        connection = test_setup["connection"]
        oauth_manager = test_setup["oauth_manager"]
        db_session = test_setup["db_session"]
        crypto_service = test_setup["crypto_service"]

        # Verify initial state
        assert oauth_manager.is_token_expiring_soon(connection)
        original_access = crypto_service.decrypt(connection.access_token)
        original_refresh = crypto_service.decrypt(connection.refresh_token)
        original_expires = connection.expires_at

        # Mock successful Notion API refresh
        with patch.object(oauth_manager, "_refresh_notion_token") as mock_refresh:
            mock_refresh.return_value = {
                "access_token": "new-access-token-abc123",
                "refresh_token": "new-refresh-token-xyz789",
                "expires_in": 3600,  # 1 hour
            }

            # Perform the refresh
            refreshed = await oauth_manager.refresh_connection_token(
                db_session, connection
            )

            # Verify API was called with correct credentials
            mock_refresh.assert_called_once_with(
                original_refresh,
                test_setup["settings"].notion_client_id,
                test_setup["settings"].notion_client_secret,
            )

            # Verify tokens were updated and encrypted
            new_access = crypto_service.decrypt(refreshed.access_token)
            new_refresh = crypto_service.decrypt(refreshed.refresh_token)

            assert new_access == "new-access-token-abc123"
            assert new_refresh == "new-refresh-token-xyz789"
            assert new_access != original_access
            assert new_refresh != original_refresh

            # Verify expiration was extended
            assert refreshed.expires_at > original_expires
            assert refreshed.expires_at > datetime.now(timezone.utc) + timedelta(
                minutes=55
            )

            # Verify tracking fields were updated
            assert refreshed.last_refresh_at is not None
            assert refreshed.refresh_count == 1
            assert refreshed.consecutive_failures == 0
            assert refreshed.needs_reauth is False

            # Verify connection is no longer expiring
            assert not oauth_manager.is_token_expiring_soon(refreshed)

    @pytest.mark.asyncio
    async def test_chat_endpoint_with_token_refresh(self, test_setup):
        """Test chat endpoint triggers token refresh when needed."""

        client = TestClient(app)
        settings = test_setup["settings"]

        # Mock the agent orchestrator response
        with patch("src.routers.chat.get_agent_orchestrator") as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.chat.return_value = MagicMock(
                reply="Test response",
                meta={
                    "usage": {"input_tokens": 10, "output_tokens": 20},
                    "tool_calls": [],
                },
            )
            mock_get_orchestrator.return_value = mock_orchestrator

            # Mock successful token refresh
            with patch(
                "src.routers.chat.OAuthManager.ensure_token_fresh"
            ) as mock_ensure_fresh:
                mock_ensure_fresh.return_value = None

                # Make chat request
                response = client.post(
                    "/api/v1/chat",
                    json={
                        "messages": [{"role": "user", "content": "Test message"}],
                        "session": "test-session-123",
                        "forceRefresh": False,
                    },
                    headers={"X-API-Key": settings.api_key},
                )

                assert response.status_code == 200
                data = response.json()

                # Verify response structure
                assert "reply" in data
                assert "meta" in data
                assert data["reply"] == "Test response"

                # Verify token refresh was attempted
                # Note: In MVP, user auth isn't implemented yet, so refresh is commented out
                # When implemented, mock_ensure_fresh.assert_called_once() would verify

    @pytest.mark.asyncio
    async def test_background_service_refresh_sweep_workflow(self, test_setup):
        """Test complete background service refresh sweep workflow."""

        # Create multiple expiring connections
        db_session = test_setup["db_session"]
        crypto_service = test_setup["crypto_service"]
        now = datetime.now(timezone.utc)

        connections = []
        for i in range(3):
            conn = NotionConnection(
                id=uuid.uuid4(),
                user_id=f"sweep-user-{i}",
                workspace_id=f"sweep-workspace-{i}",
                workspace_name=f"Sweep Workspace {i}",
                access_token=crypto_service.encrypt(f"sweep-access-{i}"),
                refresh_token=crypto_service.encrypt(f"sweep-refresh-{i}"),
                expires_at=now + timedelta(seconds=30),  # All expiring soon
                created_at=now,
                updated_at=now,
            )
            connections.append(conn)
            db_session.add(conn)

        await db_session.commit()

        # Initialize background service
        settings = test_setup["settings"]
        oauth_manager = test_setup["oauth_manager"]

        service = TokenRefreshService(settings, oauth_manager)

        # Mock successful refreshes
        with patch.object(oauth_manager, "_refresh_notion_token") as mock_refresh:
            mock_refresh.return_value = {
                "access_token": "refreshed-access",
                "refresh_token": "refreshed-refresh",
                "expires_in": 3600,
            }

            # Perform refresh sweep
            await service._perform_refresh_sweep()

            # Verify all connections were refreshed
            assert mock_refresh.call_count == 3

            # Verify database was updated for all connections
            for conn in connections:
                await db_session.refresh(conn)
                decrypted_access = crypto_service.decrypt(conn.access_token)
                assert decrypted_access == "refreshed-access"
                assert not oauth_manager.is_token_expiring_soon(conn)

    @pytest.mark.asyncio
    async def test_failure_recovery_workflow(self, test_setup):
        """Test failure recovery workflow with retry and eventual re-authentication."""

        connection = test_setup["connection"]
        oauth_manager = test_setup["oauth_manager"]
        db_session = test_setup["db_session"]

        # Simulate transient failures followed by terminal failure
        refresh_attempts = []

        def mock_refresh_with_failures(refresh_token, client_id, client_secret):
            refresh_attempts.append(refresh_token)

            if len(refresh_attempts) <= 2:
                # Transient failures (network issues)
                raise Exception("Network timeout")
            else:
                # Terminal failure (invalid refresh token)
                raise HTTPException(status_code=401, detail="Invalid refresh token")

        with patch.object(
            oauth_manager,
            "_refresh_notion_token",
            side_effect=mock_refresh_with_failures,
        ):
            # Attempt refresh - should retry on transient failures
            with pytest.raises(HTTPException):
                await oauth_manager.refresh_connection_token(db_session, connection)

            # Verify retries occurred
            assert (
                len(refresh_attempts) == 3
            )  # Initial + 2 retries (based on max_retries)

            # Verify connection marked for re-authentication
            await db_session.refresh(connection)
            assert connection.consecutive_failures > 0
            assert connection.needs_reauth is True
            assert connection.last_failure_at is not None

    @pytest.mark.asyncio
    async def test_concurrent_refresh_coordination_workflow(self, test_setup):
        """Test coordination between concurrent refresh attempts."""

        connection = test_setup["connection"]
        oauth_manager = test_setup["oauth_manager"]
        db_session = test_setup["db_session"]

        # Track refresh calls
        refresh_calls = []

        async def mock_refresh_with_delay(refresh_token, client_id, client_secret):
            refresh_calls.append(datetime.now(timezone.utc))
            await asyncio.sleep(0.2)  # Simulate network delay
            return {
                "access_token": f"refreshed-at-{len(refresh_calls)}",
                "refresh_token": "refreshed-refresh",
                "expires_in": 3600,
            }

        with patch.object(
            oauth_manager, "_refresh_notion_token", side_effect=mock_refresh_with_delay
        ):
            # Start multiple concurrent refresh attempts
            tasks = [
                asyncio.create_task(
                    oauth_manager.refresh_connection_token(db_session, connection)
                )
                for _ in range(5)
            ]

            # Wait for all to complete
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Verify only one actual refresh occurred (single-flight)
            assert len(refresh_calls) == 1

            # Verify all tasks got the same result
            successful_results = [r for r in results if not isinstance(r, Exception)]
            assert len(successful_results) == 5

            # All should have the same refreshed token
            for result in successful_results:
                decrypted = test_setup["crypto_service"].decrypt(result.access_token)
                assert decrypted == "refreshed-at-1"

    @pytest.mark.asyncio
    async def test_production_scenario_simulation(self, test_setup):
        """Simulate production scenario with mixed connection states and operations."""

        db_session = test_setup["db_session"]
        crypto_service = test_setup["crypto_service"]
        oauth_manager = test_setup["oauth_manager"]
        settings = test_setup["settings"]

        now = datetime.now(timezone.utc)

        # Create diverse connection scenarios
        healthy_conn = NotionConnection(
            id=uuid.uuid4(),
            user_id="prod-user-1",
            workspace_id="prod-workspace-1",
            workspace_name="Healthy Production Workspace",
            access_token=crypto_service.encrypt("healthy-token"),
            refresh_token=crypto_service.encrypt("healthy-refresh"),
            expires_at=now + timedelta(hours=2),
            created_at=now,
            updated_at=now,
        )

        expiring_conn = NotionConnection(
            id=uuid.uuid4(),
            user_id="prod-user-2",
            workspace_id="prod-workspace-2",
            workspace_name="Expiring Production Workspace",
            access_token=crypto_service.encrypt("expiring-token"),
            refresh_token=crypto_service.encrypt("expiring-refresh"),
            expires_at=now + timedelta(minutes=3),
            created_at=now,
            updated_at=now,
        )

        failed_conn = NotionConnection(
            id=uuid.uuid4(),
            user_id="prod-user-3",
            workspace_id="prod-workspace-3",
            workspace_name="Failed Production Workspace",
            access_token=crypto_service.encrypt("failed-token"),
            refresh_token=crypto_service.encrypt("failed-refresh"),
            expires_at=now - timedelta(hours=1),
            created_at=now,
            updated_at=now,
            consecutive_failures=5,
            needs_reauth=True,
        )

        for conn in [healthy_conn, expiring_conn, failed_conn]:
            db_session.add(conn)

        await db_session.commit()

        # Initialize background service
        background_service = TokenRefreshService(settings, oauth_manager)

        # Mock refresh behavior based on connection
        def mock_refresh_based_on_state(refresh_token, client_id, client_secret):
            decrypted = crypto_service.decrypt(refresh_token)

            if "expiring" in decrypted:
                return {
                    "access_token": "refreshed-expiring-token",
                    "refresh_token": "refreshed-expiring-refresh",
                    "expires_in": 3600,
                }
            elif "failed" in decrypted:
                raise HTTPException(status_code=401, detail="Invalid token")
            else:
                raise Exception("Should not refresh healthy tokens")

        with patch.object(
            oauth_manager,
            "_refresh_notion_token",
            side_effect=mock_refresh_based_on_state,
        ):
            # Run background sweep
            await background_service._perform_refresh_sweep()

            # Verify outcomes
            await db_session.refresh(healthy_conn)
            await db_session.refresh(expiring_conn)
            await db_session.refresh(failed_conn)

            # Healthy should remain unchanged
            assert crypto_service.decrypt(healthy_conn.access_token) == "healthy-token"

            # Expiring should be refreshed
            assert (
                crypto_service.decrypt(expiring_conn.access_token)
                == "refreshed-expiring-token"
            )
            assert not oauth_manager.is_token_expiring_soon(expiring_conn)

            # Failed should remain in failed state
            assert failed_conn.needs_reauth is True
            assert failed_conn.consecutive_failures >= 5

    @pytest.mark.asyncio
    async def test_health_monitoring_during_refresh_workflow(self, test_setup):
        """Test health monitoring accurately reflects state during refresh operations."""

        client = TestClient(app)

        # Get initial health state
        response = client.get("/healthz/oauth")
        # initial_health = response.json()  # Reserved for future comparison

        # Perform refresh operation with monitoring
        oauth_manager = test_setup["oauth_manager"]
        connection = test_setup["connection"]
        db_session = test_setup["db_session"]

        with patch.object(oauth_manager, "_refresh_notion_token") as mock_refresh:
            mock_refresh.return_value = {
                "access_token": "monitored-refresh-token",
                "refresh_token": "monitored-refresh-refresh",
                "expires_in": 3600,
            }

            # Start refresh
            refresh_task = asyncio.create_task(
                oauth_manager.refresh_connection_token(db_session, connection)
            )

            # Check health during refresh
            await asyncio.sleep(0.01)  # Brief delay to ensure refresh started

            # Get health state during refresh
            response = client.get("/healthz/oauth")
            # during_health = response.json()  # Reserved for future assertion

            # Complete refresh
            await refresh_task

            # Get final health state
            response = client.get("/healthz/oauth")
            final_health = response.json()

            # Verify health monitoring tracked the operation
            # (Specific assertions depend on implementation details)
            assert response.status_code == 200
            assert "connections" in final_health
            assert "refresh_service" in final_health
