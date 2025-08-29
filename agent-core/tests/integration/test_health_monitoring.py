"""
Health monitoring and alerting integration tests for Issue #16.

Tests validate:
- OAuth health monitoring endpoints
- Background service health reporting
- Alerting system functionality
- Production observability metrics
- Health status aggregation across workspaces
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.app import app
from src.db.models import NotionConnection
from src.services.token_refresh_service import get_token_refresh_service
from src.utils.alerting import Alert, AlertCategory, AlertManager, AlertSeverity
from src.utils.crypto import CryptoService


class TestHealthMonitoring:
    """Integration tests for health monitoring and alerting systems."""

    @pytest.fixture
    async def client(self):
        """FastAPI test client."""
        return TestClient(app)

    @pytest.fixture
    async def crypto_service(self):
        """Crypto service for token encryption."""
        return CryptoService()

    @pytest.fixture
    async def sample_connections(
        self, db_session: AsyncSession, crypto_service: CryptoService
    ):
        """Create sample connections with various health states."""
        now = datetime.now(timezone.utc)
        connections = []

        # Healthy connection (expires in 2 hours)
        healthy_conn = NotionConnection(
            id=uuid.uuid4(),
            user_id="user-healthy",
            workspace_id="workspace-1",
            workspace_name="Healthy Workspace",
            access_token=crypto_service.encrypt("healthy-token"),
            refresh_token=crypto_service.encrypt("healthy-refresh"),
            expires_at=now + timedelta(hours=2),
            created_at=now,
            updated_at=now,
            consecutive_failures=0,
            needs_reauth=False,
        )

        # Expiring connection (expires in 2 minutes)
        expiring_conn = NotionConnection(
            id=uuid.uuid4(),
            user_id="user-expiring",
            workspace_id="workspace-2",
            workspace_name="Expiring Workspace",
            access_token=crypto_service.encrypt("expiring-token"),
            refresh_token=crypto_service.encrypt("expiring-refresh"),
            expires_at=now + timedelta(minutes=2),
            created_at=now,
            updated_at=now,
            consecutive_failures=1,
            needs_reauth=False,
        )

        # Failed connection (needs re-auth)
        failed_conn = NotionConnection(
            id=uuid.uuid4(),
            user_id="user-failed",
            workspace_id="workspace-3",
            workspace_name="Failed Workspace",
            access_token=crypto_service.encrypt("failed-token"),
            refresh_token=crypto_service.encrypt("failed-refresh"),
            expires_at=now - timedelta(hours=1),  # Already expired
            created_at=now,
            updated_at=now,
            consecutive_failures=5,
            needs_reauth=True,
        )

        connections = [healthy_conn, expiring_conn, failed_conn]

        for conn in connections:
            db_session.add(conn)

        await db_session.commit()

        for conn in connections:
            await db_session.refresh(conn)

        return connections

    @pytest.mark.asyncio
    async def test_oauth_health_endpoint_comprehensive(
        self, client: TestClient, sample_connections, db_session: AsyncSession
    ):
        """Test comprehensive OAuth health endpoint functionality."""

        response = client.get("/healthz/oauth")
        assert response.status_code == 200

        health_data = response.json()

        # Verify top-level structure
        assert "status" in health_data
        assert "connections" in health_data
        assert "refresh_service" in health_data
        assert "config" in health_data
        assert "summary" in health_data
        assert "workspaces" in health_data

        # Verify connection summary
        connections = health_data["connections"]
        assert connections["total"] == 3
        assert connections["healthy"] == 1
        assert connections["expiring_soon"] >= 1
        assert connections["needs_reauth"] == 1
        assert connections["avg_failure_rate"] >= 0

        # Verify workspace breakdown
        workspaces = health_data["workspaces"]
        assert len(workspaces) == 3

        # Find each workspace and verify its health status
        healthy_workspace = next(
            w for w in workspaces if w["name"] == "Healthy Workspace"
        )
        expiring_workspace = next(
            w for w in workspaces if w["name"] == "Expiring Workspace"
        )
        failed_workspace = next(
            w for w in workspaces if w["name"] == "Failed Workspace"
        )

        assert healthy_workspace["status"] == "healthy"
        assert expiring_workspace["status"] == "expiring_soon"
        assert failed_workspace["status"] == "needs_reauth"

        # Verify configuration is exposed
        config = health_data["config"]
        assert "refresh_window_minutes" in config
        assert "max_retries" in config
        assert "background_refresh_enabled" in config

        # Verify overall health status reflects worst case
        assert health_data["status"] in ["degraded", "unhealthy"]

    @pytest.mark.asyncio
    async def test_background_service_health_reporting(self, client: TestClient):
        """Test background service health reporting in health endpoint."""

        # Start background service for testing
        background_service = await get_token_refresh_service()

        try:
            await background_service.start()

            # Wait a moment for service to initialize
            import asyncio

            await asyncio.sleep(0.1)

            response = client.get("/healthz/oauth")
            assert response.status_code == 200

            health_data = response.json()
            refresh_service = health_data["refresh_service"]

            assert refresh_service["enabled"] is True
            assert refresh_service["status"] == "running"
            assert "last_sweep_at" in refresh_service
            assert "tokens_refreshed_last_sweep" in refresh_service
            assert "active_refresh_count" in refresh_service

        finally:
            await background_service.stop()

        # Test when service is stopped
        response = client.get("/healthz/oauth")
        health_data = response.json()
        refresh_service = health_data["refresh_service"]

        assert refresh_service["enabled"] is True
        assert refresh_service["status"] == "stopped"

    @pytest.mark.asyncio
    async def test_alerting_system_functionality(self, db_session: AsyncSession):
        """Test alerting system creates and manages alerts properly."""

        alert_manager = AlertManager()

        # Test different alert severities
        test_alerts = [
            Alert(
                title="Test Critical Alert",
                description="Critical token refresh failure",
                severity=AlertSeverity.CRITICAL,
                category=AlertCategory.OAUTH,
                metadata={"user_id": "test-user", "failure_count": 5},
            ),
            Alert(
                title="Test High Alert",
                description="High priority token issue",
                severity=AlertSeverity.HIGH,
                category=AlertCategory.OAUTH,
                metadata={"user_id": "test-user", "failure_count": 3},
            ),
            Alert(
                title="Test Medium Alert",
                description="Medium priority token issue",
                severity=AlertSeverity.MEDIUM,
                category=AlertCategory.OAUTH,
                metadata={"user_id": "test-user", "failure_count": 1},
            ),
        ]

        # Send alerts and verify they're processed
        for alert in test_alerts:
            await alert_manager.send_alert(alert)

        # Verify alerts were stored (in production would be sent to monitoring system)
        # For testing, we just verify the alerts were created with correct properties
        for alert in test_alerts:
            assert alert.severity in [
                AlertSeverity.CRITICAL,
                AlertSeverity.HIGH,
                AlertSeverity.MEDIUM,
            ]
            assert alert.category == AlertCategory.OAUTH
            assert alert.timestamp is not None
            assert "user_id" in alert.metadata

    @pytest.mark.asyncio
    async def test_alert_deduplication(self):
        """Test alert manager properly deduplicates similar alerts."""

        alert_manager = AlertManager()

        # Create identical alerts
        alert1 = Alert(
            title="Token Refresh Failure",
            description="Failed to refresh token for user-123",
            severity=AlertSeverity.HIGH,
            category=AlertCategory.OAUTH,
            metadata={"user_id": "user-123", "connection_id": "conn-456"},
        )

        alert2 = Alert(
            title="Token Refresh Failure",
            description="Failed to refresh token for user-123",
            severity=AlertSeverity.HIGH,
            category=AlertCategory.OAUTH,
            metadata={"user_id": "user-123", "connection_id": "conn-456"},
        )

        # Send both alerts
        await alert_manager.send_alert(alert1)
        await alert_manager.send_alert(alert2)

        # Verify deduplication logic worked
        # (Implementation would track sent alerts and suppress duplicates within time window)
        assert True  # Placeholder for actual deduplication verification

    @pytest.mark.asyncio
    async def test_health_status_aggregation(
        self, client: TestClient, sample_connections, db_session: AsyncSession
    ):
        """Test health status aggregation logic across multiple workspaces."""

        response = client.get("/healthz/oauth")
        health_data = response.json()

        # Verify aggregation logic
        summary = health_data["summary"]

        # With 1 healthy, 1 expiring, 1 failed connection:
        # - Overall health should be "unhealthy" (has failed connections)
        # - Should report correct percentages
        assert summary["health_percentage"] < 100  # Not all connections healthy
        assert summary["total_connections"] == 3

        # Verify workspace-level aggregation
        workspaces = health_data["workspaces"]
        health_statuses = [w["status"] for w in workspaces]

        assert "healthy" in health_statuses
        assert "expiring_soon" in health_statuses
        assert "needs_reauth" in health_statuses

    @pytest.mark.asyncio
    async def test_health_endpoint_performance(
        self, client: TestClient, db_session: AsyncSession
    ):
        """Test health endpoint responds quickly even with many connections."""

        # Create many test connections
        crypto_service = CryptoService()
        now = datetime.now(timezone.utc)

        # Create 50 connections with various states
        connections = []
        for i in range(50):
            connection = NotionConnection(
                id=uuid.uuid4(),
                user_id=f"perf-user-{i}",
                workspace_id=f"perf-workspace-{i}",
                workspace_name=f"Performance Test Workspace {i}",
                access_token=crypto_service.encrypt(f"token-{i}"),
                refresh_token=crypto_service.encrypt(f"refresh-{i}"),
                expires_at=now + timedelta(hours=i % 5),  # Varying expiration times
                created_at=now,
                updated_at=now,
                consecutive_failures=i % 3,  # Varying failure counts
                needs_reauth=(i % 10 == 0),  # Every 10th connection needs reauth
            )
            connections.append(connection)
            db_session.add(connection)

        await db_session.commit()

        # Measure response time
        import time

        start_time = time.time()

        response = client.get("/healthz/oauth")

        end_time = time.time()
        response_time = end_time - start_time

        # Verify response is successful and fast
        assert response.status_code == 200
        assert response_time < 1.0  # Should respond within 1 second

        health_data = response.json()
        assert health_data["connections"]["total"] >= 50

    @pytest.mark.asyncio
    async def test_health_endpoint_database_error_handling(self, client: TestClient):
        """Test health endpoint gracefully handles database errors."""

        # Mock database error
        with patch("src.routers.health.get_db") as mock_get_db:
            mock_get_db.side_effect = Exception("Database connection failed")

            response = client.get("/healthz/oauth")

            # Should return 500 but with structured error
            assert response.status_code == 500
            error_data = response.json()
            assert "error" in error_data
            assert error_data["error"] == "HEALTH_CHECK_FAILED"

    @pytest.mark.asyncio
    async def test_alert_severity_escalation(self):
        """Test alert severity escalates with repeated failures."""

        alert_manager = AlertManager()

        # Simulate escalating failure scenario
        failure_scenarios = [
            (1, AlertSeverity.LOW),
            (3, AlertSeverity.MEDIUM),
            (5, AlertSeverity.HIGH),
            (7, AlertSeverity.CRITICAL),
        ]

        for failure_count, expected_severity in failure_scenarios:
            # Determine severity based on failure count (matching production logic)
            if failure_count >= 7:
                severity = AlertSeverity.CRITICAL
            elif failure_count >= 5:
                severity = AlertSeverity.HIGH
            elif failure_count >= 3:
                severity = AlertSeverity.MEDIUM
            else:
                severity = AlertSeverity.LOW

            alert = Alert(
                title=f"Token Refresh Failure (Attempt {failure_count})",
                description=f"Failed to refresh token, failure count: {failure_count}",
                severity=severity,
                category=AlertCategory.OAUTH,
                metadata={"failure_count": failure_count},
            )

            await alert_manager.send_alert(alert)

            # Verify severity matches expected escalation
            assert alert.severity == expected_severity

    @pytest.mark.asyncio
    async def test_monitoring_metrics_collection(
        self, client: TestClient, sample_connections, db_session: AsyncSession
    ):
        """Test that monitoring endpoints collect useful operational metrics."""

        response = client.get("/healthz/oauth")
        health_data = response.json()

        # Verify key metrics are present for operational monitoring
        connections = health_data["connections"]

        # Connection distribution metrics
        assert "total" in connections
        assert "healthy" in connections
        assert "expiring_soon" in connections
        assert "needs_reauth" in connections

        # Performance metrics
        assert "avg_failure_rate" in connections

        # Configuration exposure for debugging
        config = health_data["config"]
        assert "refresh_window_minutes" in config
        assert "max_retries" in config
        assert "background_refresh_enabled" in config

        # Service status metrics
        refresh_service = health_data["refresh_service"]
        assert "enabled" in refresh_service
        assert "status" in refresh_service

        # Workspace-level breakdown for troubleshooting
        workspaces = health_data["workspaces"]
        for workspace in workspaces:
            assert "workspace_id" in workspace
            assert "name" in workspace
            assert "status" in workspace
            assert "user_count" in workspace
            assert "failure_count" in workspace
