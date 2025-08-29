"""
Health check endpoints for service monitoring.

Provides /healthz endpoint for load balancers and monitoring systems
to verify the service is running and healthy.
"""

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, status

from src.config import Settings, get_settings
from src.utils.logging import get_logger

# Create router for health endpoints
router = APIRouter()
logger = get_logger(__name__)


@router.get(
    "/healthz",
    response_model=Dict[str, str],
    status_code=status.HTTP_200_OK,
    summary="Health check",
    description="Returns service health status and version information",
    response_description="Service is healthy",
)
async def health_check(
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> Dict[str, str]:
    """
    Health check endpoint for monitoring and load balancer probes.

    Returns:
        Dict containing status and version information

    Example response:
        {"status": "ok", "version": "0.1.0"}
    """
    # Log health check (at debug level to avoid noise)
    logger.debug("Health check requested")

    # TODO: Add deeper health checks in the future:
    # - Database connectivity (Week 3)
    # - MCP service availability (Week 1)
    # - Cache service status (Week 1/3)

    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.app_env,
    }


@router.get(
    "/healthz/live",
    response_model=Dict[str, str],
    status_code=status.HTTP_200_OK,
    summary="Liveness probe",
    description="Kubernetes liveness probe - checks if service is running",
    include_in_schema=False,  # Hide from docs as it's for k8s
)
async def liveness_probe() -> Dict[str, str]:
    """
    Liveness probe for Kubernetes deployments.
    Returns 200 if the service is alive, regardless of dependencies.
    """
    return {"status": "alive"}


@router.get(
    "/healthz/ready",
    response_model=Dict[str, bool],
    status_code=status.HTTP_200_OK,
    summary="Readiness probe",
    description="Kubernetes readiness probe - checks if service is ready to serve traffic",
    include_in_schema=False,  # Hide from docs as it's for k8s
)
async def readiness_probe() -> Dict[str, bool]:
    """
    Readiness probe for Kubernetes deployments.
    Checks if all dependencies are available and service is ready.
    """
    # TODO: Implement actual readiness checks
    # - MCP connections established
    # - Database reachable
    # - Cache service available

    return {"ready": True}


@router.get(
    "/healthz/mcp",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="MCP servers health",
    description="Returns health status of all connected MCP servers",
    response_description="MCP servers health summary",
)
async def mcp_health() -> Dict[str, Any]:
    """
    MCP servers health check endpoint.

    Shows the health status of all configured MCP servers including:
    - Connection status (healthy/unhealthy/degraded)
    - Server-specific health metrics
    - Average latency across healthy servers
    - Cache statistics

    Returns:
        Dict containing MCP health summary and per-server status

    Example response:
        {
            "status": "healthy",
            "healthy_servers": 3,
            "total_servers": 3,
            "average_latency_ms": 45.2,
            "servers": {
                "time": {
                    "status": "healthy",
                    "last_success": "2024-01-20T10:30:00",
                    "latency_ms": 42.1,
                    "error": null
                },
                ...
            },
            "cache": {
                "total_entries": 3,
                "total_hits": 125,
                "ttl_seconds": 600
            }
        }
    """
    # Import here to avoid circular dependency
    from src.services.mcp_router import get_mcp_router

    try:
        # Get the MCP router instance
        router_instance = await get_mcp_router()

        # Get health summary
        health_summary = await router_instance.get_health_summary()

        # Get cache statistics
        cache_stats = router_instance.get_cache_stats()

        # Combine health and cache info
        return {
            **health_summary,
            "cache": cache_stats,
        }

    except Exception as e:
        logger.error("Failed to get MCP health", error=str(e))
        return {
            "status": "error",
            "error": str(e),
            "healthy_servers": 0,
            "total_servers": 0,
        }


@router.get(
    "/mcp/tools",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="List MCP tools",
    description="Returns all available tools from connected MCP servers",
    response_description="Unified tool registry",
)
async def list_mcp_tools(
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    List all available MCP tools.

    Returns a unified registry of all tools discovered from connected
    MCP servers. Tools are cached for performance with configurable TTL.

    Args:
        force_refresh: Bypass cache and force fresh discovery

    Returns:
        Dict containing tools organized by server

    Example response:
        {
            "total_tools": 15,
            "servers": {
                "time": [
                    {
                        "name": "time_get_current_time",
                        "original_name": "get_current_time",
                        "description": "Get the current time",
                        "input_schema": {...}
                    }
                ],
                ...
            }
        }
    """
    # Import here to avoid circular dependency
    from src.services.mcp_router import get_mcp_router

    try:
        # Get the MCP router instance
        router_instance = await get_mcp_router()

        # Discover all tools
        tools_by_server = await router_instance.discover_all_tools(
            force_refresh=force_refresh
        )

        # Count total tools
        total_tools = sum(len(tools) for tools in tools_by_server.values())

        # Convert ToolDef objects to dicts for JSON response
        tools_dict = {}
        for server, tools in tools_by_server.items():
            tools_dict[server] = [
                {
                    "name": tool.name,
                    "original_name": tool.original_name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                    "output_schema": tool.output_schema,
                }
                for tool in tools
            ]

        return {
            "total_tools": total_tools,
            "servers": tools_dict,
            "cache_stats": router_instance.get_cache_stats(),
        }

    except Exception as e:
        logger.error("Failed to list MCP tools", error=str(e))
        return {
            "error": str(e),
            "total_tools": 0,
            "servers": {},
        }


@router.get(
    "/healthz/oauth",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="OAuth connections health",
    description="Returns health status and metrics for OAuth token refresh system",
    response_description="OAuth system health and token refresh metrics",
)
async def oauth_health() -> Dict[str, Any]:
    """
    OAuth connections health check endpoint for Issue #16.

    Shows comprehensive health status of the OAuth token refresh system:
    - Token refresh metrics and success rates
    - Connections requiring attention (expiring soon, failed refresh, needs re-auth)
    - System-wide refresh statistics and latency metrics
    - Connection distribution by workspace and failure status

    This endpoint enables production monitoring and alerting for OAuth token health.

    Returns:
        Dict containing OAuth health summary and detailed metrics

    Example response:
        {
            "status": "healthy",
            "refresh_metrics": {
                "refresh_attempts_total": 45,
                "refresh_success_total": 42,
                "success_rate": 0.933,
                "avg_latency_ms": 125.4,
                "failures_by_reason": {
                    "transient": 2,
                    "terminal": 1
                },
                "tokens_expiring_soon": 3,
                "preflight_refresh_rate": 0.067
            },
            "connection_health": {
                "total_connections": 12,
                "healthy_connections": 9,
                "needs_attention": 3,
                "refresh_capable": 8,
                "expiring_soon": 3,
                "needs_reauth": 1,
                "high_failure_rate": 1
            },
            "workspaces": [
                {
                    "workspace_id": "abc123",
                    "workspace_name": "My Team",
                    "connections": 2,
                    "healthy": 1,
                    "issues": ["token_expiring_soon"]
                }
            ]
        }
    """
    from src.db import get_db
    from src.services.oauth_manager import OAuthManager
    from src.utils.crypto import CryptoService

    try:
        # Get dependencies (simulate dependency injection for health check)
        settings = get_settings()
        crypto_service = CryptoService(settings.fernet_key)
        oauth_manager = OAuthManager(settings, crypto_service)

        # Get database connection
        async with get_db() as db:
            # Get all active connections for analysis
            from sqlalchemy import select

            from src.db.models import NotionConnection

            # Query all active connections with stats
            stmt = select(NotionConnection).where(NotionConnection.revoked_at.is_(None))
            result = await db.execute(stmt)
            all_connections = list(result.scalars().all())

            # Analyze connection health
            total_connections = len(all_connections)
            refresh_capable = len([c for c in all_connections if c.supports_refresh])
            healthy_connections = 0
            expiring_soon = 0
            needs_reauth = 0
            high_failure_rate = 0
            workspaces = {}

            # Check each connection's health status
            for conn in all_connections:
                # Check if expiring soon (within 5 minutes with jitter tolerance)
                is_expiring = oauth_manager.is_token_expiring_soon(conn)
                if is_expiring:
                    expiring_soon += 1

                # Check re-auth requirement
                if conn.needs_reauth:
                    needs_reauth += 1

                # Check failure rate using configurable threshold (warn at 80% of max)
                failure_warning_threshold = max(
                    2, int(settings.oauth_max_failure_count * 0.8)
                )
                if conn.refresh_failure_count > failure_warning_threshold:
                    high_failure_rate += 1

                # Connection is healthy if not expiring, not needing reauth, and low failures
                is_healthy = (
                    not is_expiring
                    and not conn.needs_reauth
                    and conn.refresh_failure_count <= 1
                )
                if is_healthy:
                    healthy_connections += 1

                # Group by workspace for summary
                workspace_id = conn.workspace_id
                if workspace_id not in workspaces:
                    workspaces[workspace_id] = {
                        "workspace_id": workspace_id,
                        "workspace_name": conn.workspace_name or "Unknown",
                        "connections": 0,
                        "healthy": 0,
                        "issues": set(),
                    }

                workspaces[workspace_id]["connections"] += 1
                if is_healthy:
                    workspaces[workspace_id]["healthy"] += 1

                # Track issues per workspace
                if is_expiring:
                    workspaces[workspace_id]["issues"].add("token_expiring_soon")
                if conn.needs_reauth:
                    workspaces[workspace_id]["issues"].add("needs_reauth")
                if conn.refresh_failure_count > failure_warning_threshold:
                    workspaces[workspace_id]["issues"].add("high_failure_rate")

            # Convert workspace issues sets to lists for JSON serialization
            workspace_list = []
            for ws in workspaces.values():
                ws_copy = ws.copy()
                ws_copy["issues"] = list(ws["issues"])
                workspace_list.append(ws_copy)

            # Get refresh metrics from OAuth manager
            refresh_metrics = oauth_manager.refresh_metrics.get_metrics_summary()

            # Calculate needs_attention count using configurable threshold
            needs_attention = len(
                [
                    c
                    for c in all_connections
                    if oauth_manager.is_token_expiring_soon(c)
                    or c.needs_reauth
                    or c.refresh_failure_count > failure_warning_threshold
                ]
            )

            # Determine overall health status
            health_status = "healthy"
            if needs_reauth > 0:
                health_status = "degraded"  # Some connections need user re-auth
            elif expiring_soon > 3:
                health_status = "warning"  # Many tokens expiring soon
            elif refresh_metrics["success_rate"] < 0.8:
                health_status = "warning"  # Low success rate

            return {
                "status": health_status,
                "refresh_metrics": refresh_metrics,
                "connection_health": {
                    "total_connections": total_connections,
                    "healthy_connections": healthy_connections,
                    "needs_attention": needs_attention,
                    "refresh_capable": refresh_capable,
                    "expiring_soon": expiring_soon,
                    "needs_reauth": needs_reauth,
                    "high_failure_rate": high_failure_rate,
                },
                "workspaces": workspace_list,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "check_window_minutes": 5,  # Token expiry check window
            }

    except Exception as e:
        logger.error("Failed to get OAuth health status", error=str(e))
        return {
            "status": "error",
            "error": str(e),
            "refresh_metrics": {},
            "connection_health": {
                "total_connections": 0,
                "healthy_connections": 0,
                "needs_attention": 0,
                "refresh_capable": 0,
                "expiring_soon": 0,
                "needs_reauth": 0,
                "high_failure_rate": 0,
            },
            "workspaces": [],
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }


@router.get(
    "/healthz/token-refresh-service",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Token refresh background service health",
    description="Returns health and statistics for the background token refresh service",
    response_description="Background token refresh service status and metrics",
)
async def token_refresh_service_health() -> Dict[str, Any]:
    """
    Background token refresh service health check endpoint for Issue #16 Phase 4.

    Shows the health status and operational metrics of the background token refresh service:
    - Service running status and last activity
    - Sweep statistics and performance metrics
    - Current refresh operations in progress
    - Service configuration and timing parameters
    - Error rates and success metrics

    This endpoint enables monitoring of the hybrid refresh strategy implementation.

    Returns:
        Dict containing background service health and operational metrics

    Example response:
        {
            "status": "healthy",
            "service_running": true,
            "stats": {
                "sweeps_completed": 24,
                "connections_processed": 156,
                "tokens_refreshed": 12,
                "errors_encountered": 0,
                "avg_sweep_duration_ms": 145.7,
                "last_sweep_time": "2024-01-20T15:30:45.123Z"
            },
            "current_operations": {
                "connections_in_progress": 2,
                "active_refreshes": ["conn123", "conn456"]
            },
            "configuration": {
                "sweep_interval_base": 180,
                "batch_size": 20,
                "max_concurrent_refreshes": 5
            }
        }
    """
    try:
        # Import here to avoid circular dependency and handle service not available
        from src.services.token_refresh_service import get_token_refresh_service

        # Get the background service instance
        background_service = await get_token_refresh_service()
        service_stats = background_service.get_service_stats()

        # Determine health status
        health_status = "healthy"
        if not service_stats["is_running"]:
            health_status = "stopped"
        elif service_stats.get("errors_encountered", 0) > 10:
            health_status = "degraded"  # High error rate
        elif service_stats.get("last_sweep_time") is None:
            health_status = "starting"  # No sweeps completed yet

        return {
            "status": health_status,
            "service_running": service_stats["is_running"],
            "stats": {
                "sweeps_completed": service_stats["sweeps_completed"],
                "connections_processed": service_stats["connections_processed"],
                "tokens_refreshed": service_stats["tokens_refreshed"],
                "errors_encountered": service_stats["errors_encountered"],
                "avg_sweep_duration_ms": round(
                    service_stats["avg_sweep_duration_ms"], 2
                ),
                "last_sweep_time": service_stats["last_sweep_time"],
            },
            "current_operations": {
                "connections_in_progress": service_stats["connections_in_progress"],
            },
            "configuration": service_stats["config"],
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    except ImportError:
        # Background service not available (development mode)
        return {
            "status": "unavailable",
            "service_running": False,
            "message": "Background token refresh service not enabled",
            "stats": {
                "sweeps_completed": 0,
                "connections_processed": 0,
                "tokens_refreshed": 0,
                "errors_encountered": 0,
                "avg_sweep_duration_ms": 0.0,
                "last_sweep_time": None,
            },
            "current_operations": {
                "connections_in_progress": 0,
            },
            "configuration": {},
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error("Failed to get token refresh service health", error=str(e))
        return {
            "status": "error",
            "service_running": False,
            "error": str(e),
            "stats": {
                "sweeps_completed": 0,
                "connections_processed": 0,
                "tokens_refreshed": 0,
                "errors_encountered": 1,
                "avg_sweep_duration_ms": 0.0,
                "last_sweep_time": None,
            },
            "current_operations": {
                "connections_in_progress": 0,
            },
            "configuration": {},
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
