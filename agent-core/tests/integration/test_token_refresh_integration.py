"""
Integration tests for token refresh functionality (Issue #16).

These tests validate the complete token refresh workflow including:
- Hybrid refresh strategy coordination (on-demand + background)
- Health monitoring and alerting system
- Configuration-driven refresh parameters
- Single-flight refresh coordination
- Production observability endpoints
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.app import app
from src.config import get_settings
from src.db.models import NotionConnection
from src.services.oauth_manager import OAuthManager
from src.services.token_refresh_service import get_token_refresh_service
from src.utils.alerting import AlertSeverity
from src.utils.crypto import CryptoService


class TestTokenRefreshIntegration:
    """Integration tests for token refresh system coordination."""

    @pytest.fixture
    async def mock_settings(self):
        """Mock settings with test-friendly refresh parameters."""
        settings = get_settings()
        # Override with test-friendly values
        settings.oauth_refresh_window_minutes = 1  # 1 minute for faster tests
        settings.oauth_refresh_jitter_seconds = 5  # Minimal jitter
        settings.oauth_refresh_max_retries = 2  # Fewer retries
        settings.oauth_background_refresh_enabled = True
        return settings

    @pytest.fixture
    async def crypto_service(self, mock_settings):
        """Create crypto service for token encryption."""
        return CryptoService()

    @pytest.fixture
    async def oauth_manager(self, mock_settings, crypto_service):
        """Create OAuth manager with mocked external calls."""
        manager = OAuthManager(mock_settings, crypto_service)
        return manager

    @pytest.fixture
    async def test_connection(self, db_session: AsyncSession, crypto_service):
        """Create a test Notion connection that's expiring soon."""
        # Create connection expiring in 30 seconds (within refresh window)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=30)

        connection = NotionConnection(
            id=uuid.uuid4(),
            user_id="test-user-123",
            workspace_id="test-workspace-456",
            workspace_name="Test Workspace",
            access_token=crypto_service.encrypt("test-access-token"),
            refresh_token=crypto_service.encrypt("test-refresh-token"),
            expires_at=expires_at,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        db_session.add(connection)
        await db_session.commit()
        await db_session.refresh(connection)
        return connection

    @pytest.mark.asyncio
    async def test_hybrid_refresh_coordination(
        self,
        db_session: AsyncSession,
        oauth_manager: OAuthManager,
        test_connection: NotionConnection,
        mock_settings,
    ):
        """Test that on-demand and background refresh coordinate properly."""

        # Mock successful token refresh response
        with patch.object(oauth_manager, "_refresh_notion_token") as mock_refresh:
            mock_refresh.return_value = {
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "expires_in": 3600,  # 1 hour
            }

            # Initialize background service
            background_service = await get_token_refresh_service()

            # Verify connection is initially expiring
            assert oauth_manager.is_token_expiring_soon(test_connection)

            # Start background service
            await background_service.start()

            try:
                # Simulate on-demand refresh attempt
                refresh_task = asyncio.create_task(
                    oauth_manager.ensure_token_fresh(
                        db_session, test_connection.user_id
                    )
                )

                # Allow brief moment for coordination
                await asyncio.sleep(0.1)

                # Background service should see the ongoing refresh
                assert background_service.is_connection_being_refreshed(
                    str(test_connection.id)
                )

                # Complete the refresh
                await refresh_task

                # Verify token was refreshed only once (no duplicate calls)
                assert mock_refresh.call_count == 1

                # Verify connection is no longer expiring
                await db_session.refresh(test_connection)
                assert not oauth_manager.is_token_expiring_soon(test_connection)

            finally:
                await background_service.stop()

    @pytest.mark.asyncio
    async def test_background_service_batch_processing(
        self, db_session: AsyncSession, oauth_manager: OAuthManager, mock_settings
    ):
        """Test background service batch processing of multiple expiring connections."""

        # Create multiple expiring connections
        connections = []
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=30)

        for i in range(5):
            connection = NotionConnection(
                id=uuid.uuid4(),
                user_id=f"test-user-{i}",
                workspace_id=f"test-workspace-{i}",
                workspace_name=f"Test Workspace {i}",
                access_token=oauth_manager.crypto_service.encrypt(f"access-token-{i}"),
                refresh_token=oauth_manager.crypto_service.encrypt(
                    f"refresh-token-{i}"
                ),
                expires_at=expires_at,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db_session.add(connection)
            connections.append(connection)

        await db_session.commit()

        # Mock successful refresh for all connections
        with patch.object(oauth_manager, "_refresh_notion_token") as mock_refresh:
            mock_refresh.return_value = {
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "expires_in": 3600,
            }

            background_service = await get_token_refresh_service()
            await background_service.start()

            try:
                # Trigger a refresh sweep
                await background_service._perform_refresh_sweep()

                # Verify all connections were refreshed
                assert mock_refresh.call_count == 5

                # Verify all connections are no longer expiring
                for connection in connections:
                    await db_session.refresh(connection)
                    assert not oauth_manager.is_token_expiring_soon(connection)

            finally:
                await background_service.stop()

    @pytest.mark.asyncio
    async def test_health_monitoring_integration(self, mock_settings):
        """Test health monitoring endpoints with real OAuth manager integration."""

        client = TestClient(app)

        # Test OAuth health endpoint
        response = client.get("/healthz/oauth")
        assert response.status_code == 200

        health_data = response.json()
        assert "status" in health_data
        assert "connections" in health_data
        assert "refresh_service" in health_data
        assert "config" in health_data

        # Verify configuration is properly exposed
        config = health_data["config"]
        assert (
            config["refresh_window_minutes"]
            == mock_settings.oauth_refresh_window_minutes
        )
        assert config["max_retries"] == mock_settings.oauth_refresh_max_retries
        assert (
            config["background_refresh_enabled"]
            == mock_settings.oauth_background_refresh_enabled
        )

    @pytest.mark.asyncio
    async def test_alerting_system_integration(
        self,
        db_session: AsyncSession,
        oauth_manager: OAuthManager,
        test_connection: NotionConnection,
    ):
        """Test alerting system integration with token refresh failures."""

        # Mock failing token refresh
        with patch.object(oauth_manager, "_refresh_notion_token") as mock_refresh:
            mock_refresh.side_effect = Exception("Simulated refresh failure")

            with patch("src.utils.alerting.get_alert_manager") as mock_get_alert:
                mock_alert_manager = AsyncMock()
                mock_get_alert.return_value = mock_alert_manager

                # Attempt refresh that should fail
                try:
                    await oauth_manager.refresh_connection_token(
                        db_session, test_connection
                    )
                    # Should not reach here
                    raise AssertionError("Expected refresh to fail")
                except Exception:
                    # Expected failure
                    pass

                # Verify alert was triggered
                mock_alert_manager.send_alert.assert_called_once()

                # Verify alert contains expected information
                call_args = mock_alert_manager.send_alert.call_args[0]
                alert = call_args[0]

                assert alert.category.value == "oauth"
                assert alert.severity in [AlertSeverity.HIGH, AlertSeverity.CRITICAL]
                assert test_connection.user_id in alert.description

    @pytest.mark.asyncio
    async def test_configuration_driven_refresh_parameters(
        self,
        db_session: AsyncSession,
        oauth_manager: OAuthManager,
        test_connection: NotionConnection,
    ):
        """Test that refresh behavior respects configuration parameters."""

        # Test with custom refresh window
        custom_window = 2  # 2 minutes

        # Connection expiring in 90 seconds (1.5 minutes)
        test_connection.expires_at = datetime.now(timezone.utc) + timedelta(seconds=90)
        await db_session.commit()

        # Should not be expiring with default 1-minute window
        assert not oauth_manager.is_token_expiring_soon(
            test_connection, window_minutes=1
        )

        # Should be expiring with 2-minute window
        assert oauth_manager.is_token_expiring_soon(
            test_connection, window_minutes=custom_window
        )

    @pytest.mark.asyncio
    async def test_single_flight_refresh_prevention(
        self,
        db_session: AsyncSession,
        oauth_manager: OAuthManager,
        test_connection: NotionConnection,
    ):
        """Test that concurrent refresh attempts are coordinated."""

        refresh_call_count = 0

        async def counting_refresh(*args, **kwargs):
            nonlocal refresh_call_count
            refresh_call_count += 1
            # Add delay to simulate network call
            await asyncio.sleep(0.1)
            return {
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "expires_in": 3600,
            }

        with patch.object(oauth_manager, "_refresh_notion_token", counting_refresh):
            # Start multiple concurrent refresh attempts
            tasks = []
            for _ in range(3):
                task = asyncio.create_task(
                    oauth_manager.refresh_connection_token(db_session, test_connection)
                )
                tasks.append(task)

            # Wait for all tasks to complete
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Verify only one actual refresh call was made (single-flight)
            assert refresh_call_count == 1

            # Verify all tasks succeeded
            for result in results:
                assert not isinstance(result, Exception)

    @pytest.mark.asyncio
    async def test_end_to_end_refresh_workflow(
        self,
        db_session: AsyncSession,
        oauth_manager: OAuthManager,
        test_connection: NotionConnection,
        mock_settings,
    ):
        """Test complete end-to-end token refresh workflow."""

        # Mock successful Notion API response
        with patch.object(oauth_manager, "_refresh_notion_token") as mock_refresh:
            mock_refresh.return_value = {
                "access_token": "refreshed-access-token",
                "refresh_token": "refreshed-refresh-token",
                "expires_in": 3600,
            }

            # Record initial state
            initial_access_token = oauth_manager.crypto_service.decrypt(
                test_connection.access_token
            )
            initial_refresh_token = oauth_manager.crypto_service.decrypt(
                test_connection.refresh_token
            )
            initial_expires_at = test_connection.expires_at

            # Perform refresh
            refreshed_connection = await oauth_manager.refresh_connection_token(
                db_session, test_connection
            )

            # Verify tokens were updated
            new_access_token = oauth_manager.crypto_service.decrypt(
                refreshed_connection.access_token
            )
            new_refresh_token = oauth_manager.crypto_service.decrypt(
                refreshed_connection.refresh_token
            )

            assert new_access_token == "refreshed-access-token"
            assert new_refresh_token == "refreshed-refresh-token"
            assert new_access_token != initial_access_token
            assert new_refresh_token != initial_refresh_token

            # Verify expiration time was updated
            assert refreshed_connection.expires_at > initial_expires_at

            # Verify connection is no longer considered expiring
            assert not oauth_manager.is_token_expiring_soon(refreshed_connection)

            # Verify last_refresh_at was updated
            assert refreshed_connection.last_refresh_at is not None
            assert refreshed_connection.last_refresh_at > initial_expires_at
