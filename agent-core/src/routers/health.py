"""
Health check endpoints for service monitoring.

Provides /healthz endpoint for load balancers and monitoring systems
to verify the service is running and healthy.
"""

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
