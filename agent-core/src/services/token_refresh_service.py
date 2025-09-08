"""
Background token refresh service for proactive OAuth token management.

This service implements the hybrid refresh strategy from Issue #16 Phase 4:
- Background scheduled refresh to maintain token freshness
- Load balancing with on-demand refresh to prevent duplicate work
- Batch processing for efficiency at scale
- Failure isolation to prevent cascading issues

The service runs as a background task and coordinates with the on-demand
refresh system to ensure optimal token freshness with minimal API calls.
"""

import asyncio
import random
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db import get_async_session
from ..db.models import NotionConnection
from ..services.oauth_manager import OAuthManager
from ..utils.alerting import get_alert_manager
from ..utils.crypto import CryptoService

logger = structlog.get_logger(__name__)


class TokenRefreshService:
    """
    Background service for proactive OAuth token refresh (Phase 4 - Issue #16).

    Implements a hybrid refresh strategy:
    1. Background sweep every 2-5 minutes to catch expiring tokens
    2. Smart scheduling based on token expiry with jitter
    3. Coordination with on-demand refresh to avoid duplication
    4. Batch processing for efficiency with large numbers of connections
    5. Failure isolation to prevent one bad connection from affecting others

    The service maintains a global view of token health and optimizes
    refresh timing to minimize API calls while ensuring token freshness.
    """

    def __init__(self, settings: Settings, crypto_service: CryptoService):
        """
        Initialize token refresh background service.

        Args:
            settings: Application configuration
            crypto_service: Crypto service for token encryption/decryption
        """
        self.settings = settings
        self.crypto_service = crypto_service
        self.oauth_manager = OAuthManager(settings, crypto_service)

        # Service state
        self._running = False
        self._background_task: Optional[asyncio.Task] = None
        self._refresh_in_progress: Set[
            str
        ] = set()  # Connection IDs currently being refreshed
        self._last_sweep_time: Optional[datetime] = None

        # Configuration for background refresh
        self.sweep_interval_base = 180  # 3 minutes base interval
        self.sweep_jitter_seconds = 60  # ±60 seconds jitter
        self.batch_size = 20  # Process up to 20 connections per batch
        self.max_concurrent_refreshes = 5  # Limit concurrent refreshes

        # Statistics for monitoring
        self.stats = {
            "sweeps_completed": 0,
            "connections_processed": 0,
            "tokens_refreshed": 0,
            "errors_encountered": 0,
            "avg_sweep_duration_ms": 0.0,
            "last_sweep_time": None,
        }

    async def start(self) -> None:
        """Start the background token refresh service."""
        if self._running:
            logger.warning("Token refresh service already running")
            return

        self._running = True
        self._background_task = asyncio.create_task(self._background_refresh_loop())

        logger.info(
            "Token refresh service started",
            sweep_interval_base=self.sweep_interval_base,
            batch_size=self.batch_size,
            max_concurrent=self.max_concurrent_refreshes,
        )

    async def stop(self) -> None:
        """Stop the background token refresh service."""
        if not self._running:
            logger.warning("Token refresh service not running")
            return

        self._running = False

        if self._background_task:
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                pass

        # Cleanup OAuth manager HTTP client
        await self.oauth_manager.http_client.aclose()

        logger.info("Token refresh service stopped")

    async def _background_refresh_loop(self) -> None:
        """Main background refresh loop with jittered intervals."""
        logger.info("Background token refresh loop starting")

        while self._running:
            try:
                # Calculate next sweep time with jitter to prevent thundering herd
                jitter = random.randint(
                    -self.sweep_jitter_seconds, self.sweep_jitter_seconds
                )
                sleep_duration = self.sweep_interval_base + jitter

                # Sleep with periodic checks for shutdown
                for _ in range(sleep_duration):
                    if not self._running:
                        break
                    await asyncio.sleep(1)

                if not self._running:
                    break

                # Perform background refresh sweep
                await self._perform_refresh_sweep()

            except Exception as e:
                logger.error(
                    "Background refresh loop error",
                    error=str(e),
                    sweep_count=self.stats["sweeps_completed"],
                )
                self.stats["errors_encountered"] += 1

                # Continue running but add delay after error
                await asyncio.sleep(30)

        logger.info("Background token refresh loop stopped")

    async def _perform_refresh_sweep(self) -> None:
        """
        Perform a single sweep of all connections to identify and refresh expiring tokens.

        This implements the core background refresh logic:
        1. Query all active connections that support refresh
        2. Filter for tokens expiring within the configured window
        3. Batch process refreshes with concurrency limits
        4. Update statistics and send alerts as needed
        """
        sweep_start_time = datetime.now(timezone.utc)
        connections_processed = 0
        tokens_refreshed = 0

        try:
            logger.debug("Starting background token refresh sweep")

            async for db in get_async_session():
                # Get all active connections that support refresh
                connections = await self._get_refresh_candidates(db)
                connections_processed = len(connections)

                if not connections:
                    logger.debug("No connections require background refresh")
                    return

                # Filter connections that aren't already being refreshed
                available_connections = [
                    conn
                    for conn in connections
                    if str(conn.id) not in self._refresh_in_progress
                ]

                logger.info(
                    "Background refresh sweep starting",
                    total_candidates=len(connections),
                    available_for_refresh=len(available_connections),
                    in_progress_count=len(self._refresh_in_progress),
                )

                # Process connections in batches with concurrency limits
                tokens_refreshed = await self._batch_refresh_tokens(
                    db, available_connections
                )

        except Exception as e:
            logger.error("Background refresh sweep failed", error=str(e))
            self.stats["errors_encountered"] += 1
        finally:
            # Update statistics
            sweep_duration = (
                datetime.now(timezone.utc) - sweep_start_time
            ).total_seconds() * 1000
            self._update_sweep_statistics(
                sweep_duration, connections_processed, tokens_refreshed
            )

    async def _get_refresh_candidates(self, db: AsyncSession) -> List[NotionConnection]:
        """
        Get all connections that are candidates for background refresh.

        Args:
            db: Database session

        Returns:
            List of connections that may need refresh
        """
        # Query all active connections that support refresh
        stmt = (
            select(NotionConnection)
            .where(
                NotionConnection.revoked_at.is_(None),  # Active connections only
                NotionConnection.supports_refresh.is_(True),  # Must support refresh
                NotionConnection.access_token_expires_at.is_not(
                    None
                ),  # Must have expiry
            )
            .order_by(NotionConnection.access_token_expires_at)
        )  # Process earliest expiry first

        result = await db.execute(stmt)
        all_candidates = list(result.scalars().all())

        # Filter to only tokens expiring within our background refresh window
        # Use a longer window for background refresh (10 minutes vs 5 for on-demand)
        background_window_minutes = self.settings.oauth_refresh_window_minutes * 2
        expiring_candidates = []

        for connection in all_candidates:
            if self.oauth_manager.is_token_expiring_soon(
                connection, background_window_minutes
            ):
                expiring_candidates.append(connection)

        logger.debug(
            "Background refresh candidates identified",
            total_with_refresh=len(all_candidates),
            expiring_soon=len(expiring_candidates),
            window_minutes=background_window_minutes,
        )

        return expiring_candidates

    async def _batch_refresh_tokens(
        self, db: AsyncSession, connections: List[NotionConnection]
    ) -> int:
        """
        Refresh tokens in batches with concurrency control.

        Args:
            db: Database session
            connections: Connections to refresh

        Returns:
            Number of tokens successfully refreshed
        """
        if not connections:
            return 0

        total_refreshed = 0
        semaphore = asyncio.Semaphore(self.max_concurrent_refreshes)

        # Process connections in batches to avoid overwhelming the database
        for i in range(0, len(connections), self.batch_size):
            batch = connections[i : i + self.batch_size]

            logger.debug(
                "Processing background refresh batch",
                batch_number=i // self.batch_size + 1,
                batch_size=len(batch),
                total_batches=(len(connections) + self.batch_size - 1)
                // self.batch_size,
            )

            # Create refresh tasks for this batch
            refresh_tasks = [
                self._refresh_single_connection(db, conn, semaphore) for conn in batch
            ]

            # Execute batch with timeout
            try:
                batch_results = await asyncio.wait_for(
                    asyncio.gather(*refresh_tasks, return_exceptions=True),
                    timeout=300,  # 5 minute timeout for batch
                )

                # Count successful refreshes
                batch_refreshed = sum(1 for result in batch_results if result is True)
                total_refreshed += batch_refreshed

                logger.debug(
                    "Background refresh batch completed",
                    batch_refreshed=batch_refreshed,
                    batch_size=len(batch),
                )

            except asyncio.TimeoutError:
                logger.error("Background refresh batch timed out")
                self.stats["errors_encountered"] += 1
            except Exception as e:
                logger.error("Background refresh batch failed", error=str(e))
                self.stats["errors_encountered"] += 1

        return total_refreshed

    async def _refresh_single_connection(
        self,
        db: AsyncSession,
        connection: NotionConnection,
        semaphore: asyncio.Semaphore,
    ) -> bool:
        """
        Refresh a single connection with concurrency control and error isolation.

        Args:
            db: Database session
            connection: Connection to refresh
            semaphore: Semaphore for concurrency control

        Returns:
            True if refresh was successful, False otherwise
        """
        connection_id = str(connection.id)

        async with semaphore:
            # Mark connection as being refreshed to avoid duplication
            self._refresh_in_progress.add(connection_id)

            try:
                # Double-check that token still needs refresh (may have been refreshed on-demand)
                await db.refresh(connection)
                if not self.oauth_manager.is_token_expiring_soon(connection):
                    logger.debug(
                        "Token already refreshed, skipping background refresh",
                        connection_id=connection_id,
                    )
                    return False

                # Check if connection needs re-auth
                if connection.needs_reauth:
                    logger.debug(
                        "Connection needs re-auth, skipping background refresh",
                        connection_id=connection_id,
                    )
                    return False

                # Perform the refresh
                logger.debug(
                    "Starting background token refresh",
                    connection_id=connection_id,
                    user_id=str(connection.user_id),
                )

                refresh_result = (
                    await self.oauth_manager.refresh_notion_token_with_backoff(
                        connection
                    )
                )

                if refresh_result.success:
                    # Update tokens and mark success
                    await self.oauth_manager.update_refreshed_tokens(
                        db, connection, refresh_result.token_response
                    )

                    logger.info(
                        "Background token refresh successful",
                        connection_id=connection_id,
                        user_id=str(connection.user_id),
                    )
                    return True
                else:
                    # Handle failure (similar to on-demand refresh)
                    is_terminal = refresh_result.classification == "terminal"
                    connection.mark_refresh_failure(is_terminal)

                    if (
                        connection.refresh_failure_count
                        >= self.settings.oauth_max_failure_count
                    ):
                        connection.needs_reauth = True

                    await db.commit()

                    # Send alert for background refresh failures
                    alert_manager = get_alert_manager()
                    alert_manager.alert_token_refresh_failure(
                        user_id=str(connection.user_id),
                        connection_id=connection_id,
                        failure_count=connection.refresh_failure_count,
                        error_message=f"Background: {refresh_result.error or 'Unknown error'}",
                        is_terminal=is_terminal,
                    )

                    logger.warning(
                        "Background token refresh failed",
                        connection_id=connection_id,
                        error=refresh_result.error,
                        classification=refresh_result.classification,
                        failure_count=connection.refresh_failure_count,
                    )
                    return False

            except Exception as e:
                logger.error(
                    "Background refresh error for connection",
                    connection_id=connection_id,
                    error=str(e),
                )
                return False
            finally:
                # Always remove from in-progress set
                self._refresh_in_progress.discard(connection_id)

    def _update_sweep_statistics(
        self, duration_ms: float, connections_processed: int, tokens_refreshed: int
    ) -> None:
        """Update sweep statistics for monitoring."""
        self.stats["sweeps_completed"] += 1
        self.stats["connections_processed"] += connections_processed
        self.stats["tokens_refreshed"] += tokens_refreshed
        self.stats["last_sweep_time"] = datetime.now(timezone.utc).isoformat()

        # Update rolling average of sweep duration
        current_avg = self.stats["avg_sweep_duration_ms"]
        sweep_count = self.stats["sweeps_completed"]
        self.stats["avg_sweep_duration_ms"] = (
            current_avg * (sweep_count - 1) + duration_ms
        ) / sweep_count

        logger.info(
            "Background refresh sweep completed",
            duration_ms=round(duration_ms, 2),
            connections_processed=connections_processed,
            tokens_refreshed=tokens_refreshed,
            total_sweeps=sweep_count,
            avg_duration_ms=round(self.stats["avg_sweep_duration_ms"], 2),
        )

    def is_connection_being_refreshed(self, connection_id: str) -> bool:
        """
        Check if a connection is currently being refreshed by background service.

        This allows on-demand refresh to skip connections already being processed.

        Args:
            connection_id: Connection ID to check

        Returns:
            True if connection is currently being refreshed in background
        """
        return connection_id in self._refresh_in_progress

    def get_service_stats(self) -> Dict[str, any]:
        """
        Get background refresh service statistics for monitoring.

        Returns:
            Dictionary with service statistics and health metrics
        """
        return {
            **self.stats,
            "is_running": self._running,
            "connections_in_progress": len(self._refresh_in_progress),
            "config": {
                "sweep_interval_base": self.sweep_interval_base,
                "sweep_jitter_seconds": self.sweep_jitter_seconds,
                "batch_size": self.batch_size,
                "max_concurrent_refreshes": self.max_concurrent_refreshes,
            },
        }


# Global service instance
_token_refresh_service: Optional[TokenRefreshService] = None


async def get_token_refresh_service() -> TokenRefreshService:
    """Get the global token refresh service instance."""
    global _token_refresh_service
    if _token_refresh_service is None:
        from ..config import get_settings

        settings = get_settings()
        crypto_service = CryptoService(settings.fernet_key)
        _token_refresh_service = TokenRefreshService(settings, crypto_service)

    return _token_refresh_service


def reset_token_refresh_service() -> None:
    """Reset token refresh service (useful for testing)."""
    global _token_refresh_service
    _token_refresh_service = None
