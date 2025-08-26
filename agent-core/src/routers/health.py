"""
Health check endpoints for service monitoring.

Provides /healthz endpoint for load balancers and monitoring systems
to verify the service is running and healthy.
"""

from typing import Dict

from fastapi import APIRouter, status

# Create router for health endpoints
router = APIRouter()


@router.get(
    "/healthz",
    response_model=Dict[str, str],
    status_code=status.HTTP_200_OK,
    summary="Health check",
    description="Returns service health status and version information",
    response_description="Service is healthy",
)
async def health_check() -> Dict[str, str]:
    """
    Health check endpoint for monitoring and load balancer probes.

    Returns:
        Dict containing status and version information

    Example response:
        {"status": "ok", "version": "0.1.0"}
    """
    # Import here to avoid circular dependency
    from src.app import APP_VERSION

    # TODO: Add deeper health checks in the future:
    # - Database connectivity (Week 3)
    # - MCP service availability (Week 1)
    # - Cache service status (Week 1/3)

    return {
        "status": "ok",
        "version": APP_VERSION,
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
