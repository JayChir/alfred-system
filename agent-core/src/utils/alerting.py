"""
Alerting utilities for production monitoring and error reporting.

This module provides structured alerting capabilities for critical system events,
particularly OAuth token refresh failures and system health issues.
"""

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger(__name__)


class AlertSeverity(str, Enum):
    """Alert severity levels for production monitoring."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertCategory(str, Enum):
    """Alert categories for organizing alerts by system component."""

    OAUTH_TOKENS = "oauth_tokens"
    DATABASE = "database"
    MCP_CONNECTIVITY = "mcp_connectivity"
    SYSTEM_HEALTH = "system_health"
    PERFORMANCE = "performance"


class Alert:
    """
    Structured alert with severity, category, and contextual information.

    Alerts are used for production monitoring and can be forwarded to
    external systems like PagerDuty, Slack, or email.
    """

    def __init__(
        self,
        title: str,
        description: str,
        severity: AlertSeverity,
        category: AlertCategory,
        metadata: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        connection_id: Optional[str] = None,
    ):
        self.title = title
        self.description = description
        self.severity = severity
        self.category = category
        self.metadata = metadata or {}
        self.user_id = user_id
        self.connection_id = connection_id
        self.timestamp = datetime.now(timezone.utc)
        self.alert_id = (
            f"{category.value}_{severity.value}_{int(self.timestamp.timestamp())}"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert alert to dictionary for JSON serialization."""
        return {
            "alert_id": self.alert_id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value,
            "category": self.category.value,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
            "user_id": self.user_id,
            "connection_id": self.connection_id,
        }

    def to_json(self) -> str:
        """Convert alert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


class AlertManager:
    """
    Production alerting manager for OAuth token refresh and system health monitoring.

    Handles alert generation, filtering, and forwarding to external systems.
    Currently logs alerts with structured logging; can be extended for external integrations.
    """

    def __init__(self):
        """Initialize alert manager with rate limiting and deduplication."""
        self._alert_counts = {}  # For rate limiting duplicate alerts

    def should_alert(self, alert: Alert) -> bool:
        """
        Determine if an alert should be sent based on severity and rate limiting.

        Args:
            alert: Alert to evaluate

        Returns:
            True if alert should be sent
        """
        # Always alert on critical issues
        if alert.severity == AlertSeverity.CRITICAL:
            return True

        # Rate limit duplicate alerts (same category + user/connection)
        rate_limit_key = f"{alert.category.value}_{alert.user_id}_{alert.connection_id}"
        current_count = self._alert_counts.get(rate_limit_key, 0)

        # For OAuth token issues, limit to 3 alerts per hour per user/connection
        if alert.category == AlertCategory.OAUTH_TOKENS:
            if current_count >= 3:
                return False

        return True

    def send_alert(self, alert: Alert) -> None:
        """
        Send alert through configured channels.

        Args:
            alert: Alert to send
        """
        if not self.should_alert(alert):
            logger.debug(
                "Alert suppressed due to rate limiting",
                alert_id=alert.alert_id,
                category=alert.category.value,
                severity=alert.severity.value,
            )
            return

        # Log structured alert for production monitoring
        logger.bind(
            alert_id=alert.alert_id,
            alert_severity=alert.severity.value,
            alert_category=alert.category.value,
            user_id=alert.user_id,
            connection_id=alert.connection_id,
        ).warning(
            f"ALERT: {alert.title}",
            description=alert.description,
            metadata=alert.metadata,
        )

        # Update rate limiting counter
        rate_limit_key = f"{alert.category.value}_{alert.user_id}_{alert.connection_id}"
        self._alert_counts[rate_limit_key] = (
            self._alert_counts.get(rate_limit_key, 0) + 1
        )

        # TODO: Add external integrations here
        # - Send to PagerDuty for critical alerts
        # - Send to Slack for medium/high alerts
        # - Send email notifications for user-specific issues

    def alert_token_refresh_failure(
        self,
        user_id: str,
        connection_id: str,
        failure_count: int,
        error_message: str,
        is_terminal: bool = False,
    ) -> None:
        """
        Create alert for OAuth token refresh failure.

        Args:
            user_id: User ID experiencing the failure
            connection_id: Connection ID that failed to refresh
            failure_count: Number of consecutive failures
            error_message: Error details from the refresh attempt
            is_terminal: Whether this is a terminal error requiring re-auth
        """
        # Determine severity based on failure count and error type
        if is_terminal or failure_count >= 5:
            severity = AlertSeverity.CRITICAL
        elif failure_count >= 3:
            severity = AlertSeverity.HIGH
        else:
            severity = AlertSeverity.MEDIUM

        alert = Alert(
            title=f"OAuth Token Refresh Failure (x{failure_count})",
            description=(
                f"Token refresh failed {failure_count} consecutive times for user {user_id}. "
                f"Error: {error_message}. "
                f"{'Requires re-authentication.' if is_terminal else 'Automatic retry will continue.'}"
            ),
            severity=severity,
            category=AlertCategory.OAUTH_TOKENS,
            metadata={
                "failure_count": failure_count,
                "error_message": error_message,
                "is_terminal": is_terminal,
                "requires_reauth": is_terminal or failure_count >= 5,
            },
            user_id=user_id,
            connection_id=connection_id,
        )

        self.send_alert(alert)

    def alert_high_token_expiry_rate(
        self, expiring_count: int, total_connections: int, threshold: int = 10
    ) -> None:
        """
        Create alert for high rate of token expiry across the system.

        Args:
            expiring_count: Number of tokens expiring soon
            total_connections: Total active connections
            threshold: Threshold for triggering alert
        """
        if expiring_count < threshold:
            return

        expiry_rate = expiring_count / max(1, total_connections)
        severity = AlertSeverity.HIGH if expiry_rate > 0.5 else AlertSeverity.MEDIUM

        alert = Alert(
            title="High Token Expiry Rate Detected",
            description=(
                f"{expiring_count} out of {total_connections} tokens are expiring soon "
                f"({expiry_rate:.1%} expiry rate). This may indicate a systemic issue "
                "or coordinated token refresh needed."
            ),
            severity=severity,
            category=AlertCategory.OAUTH_TOKENS,
            metadata={
                "expiring_count": expiring_count,
                "total_connections": total_connections,
                "expiry_rate": expiry_rate,
            },
        )

        self.send_alert(alert)

    def alert_refresh_success_rate_low(
        self, success_rate: float, total_attempts: int
    ) -> None:
        """
        Create alert for low OAuth refresh success rate.

        Args:
            success_rate: Success rate as decimal (0.0 to 1.0)
            total_attempts: Total refresh attempts in the monitoring window
        """
        if success_rate >= 0.8 or total_attempts < 5:
            return  # Only alert if success rate < 80% and enough attempts

        severity = AlertSeverity.CRITICAL if success_rate < 0.5 else AlertSeverity.HIGH

        alert = Alert(
            title="Low OAuth Refresh Success Rate",
            description=(
                f"OAuth token refresh success rate is {success_rate:.1%} "
                f"over {total_attempts} attempts. This indicates potential "
                "issues with Notion API connectivity or credential problems."
            ),
            severity=severity,
            category=AlertCategory.OAUTH_TOKENS,
            metadata={
                "success_rate": success_rate,
                "total_attempts": total_attempts,
                "failure_rate": 1.0 - success_rate,
            },
        )

        self.send_alert(alert)


# Global alert manager instance
_alert_manager: Optional[AlertManager] = None


def get_alert_manager() -> AlertManager:
    """Get the global alert manager instance."""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager()
    return _alert_manager


def reset_alert_manager() -> None:
    """Reset alert manager (useful for testing)."""
    global _alert_manager
    _alert_manager = None
